''' Textual viewer/editor for SQLite databases '''

import os
import sys
import sqlite3
from collections import namedtuple
from typing import Sequence, Optional

from textual import events
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.binding import Binding
from textual.widgets import (Button,
                             ContentSwitcher,
                             DirectoryTree,
                             DataTable,
                             Footer,
                             Header,
                             Input,
                             Label,
                             Tab,
                             Tabs,
                             Tree,
                             Static)
from textual.screen import Screen
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.coordinate import Coordinate


TableEditInfo = namedtuple('TableEditInfo', 'value column tablename conditions coordinate')


class Database:
    ''' The Sqlite Database '''

    def load(self, path: str) -> bool:
        ''' Load database from path. Return True if successful. '''
        try:
            self.connection = sqlite3.connect(path)
        except sqlite3.DatabaseError:
            return False

        self.path = path
        self.name = os.path.split(self.path)[1]
        _, tables = self.query(
            "SELECT name FROM sqlite_schema WHERE type='table'")
        self.tables = [t[0] for t in tables]
        _, views = self.query(
            "SELECT name FROM sqlite_schema WHERE type='view'")
        self.views = [v[0] for v in views]
        return True

    def query(self, query: str, args: Sequence[str] = None) -> tuple[list[str], list[list[str]]]:
        ''' Query the database '''
        args = () if args is None else args
        columns: list[str] = []
        rows: list[list[str]] = [[]]
        try:
            cursor = self.connection.execute(query, args)
        except AttributeError:
            pass
        else:
            if cursor.description:
                columns = [col[0] for col in cursor.description]
                rows = [[str(col) for col in row] for row in cursor.fetchall()]
        return columns, rows

    def table_info(self, name: str) -> tuple[list[str], list[list[str]]]:
        ''' Get table info '''
        columns, info = self.query(f'PRAGMA table_info({name});')
        return columns, info

    def table_data(self, name: str) -> tuple[list[str], list[list[str]]]:
        ''' Get column names and row data from table '''
        primary_keys = self.primary_keys(name)
        if not name in self.views and primary_keys[0] == 'rowid':
            columns, rows = self.query(f'SELECT rowid, * FROM {name}')
        else:
            columns, rows = self.query(f'SELECT * FROM {name}')
        return columns, rows

    def primary_keys(self, name: str) -> list[str]:
        ''' Get primary key columns for a table '''
        _, rows = self.query(
            f'SELECT l.name FROM pragma_table_info("{name}") as l WHERE l.pk = 1;')
        rowstrs = [r[0] for r in rows]
        if not rowstrs:
            rowstrs = ['rowid']
        return rowstrs

    def update(self, tablename: str, colunmname: str, value: str, where: dict = None) -> None:
        ''' Update a single field in the database '''
        args = [value]
        sql = f'UPDATE {tablename} SET {colunmname}=? '
        if where:
            searchstrs = ' and '.join(f'{pk}=?' for pk in where.keys())
            sql += ' WHERE ' + searchstrs
            args += where.values()
        self.connection.execute(sql, args)
        self.connection.commit()

    def insert(self, tablename: str, values: dict[str, str]) -> None:
        colstr = ','.join(values.keys())
        qs = ','.join('?'*len(values))
        sql = f'INSERT INTO {tablename} ({colstr}) VALUES({qs})'
        self.connection.execute(sql, list(values.values()))
        self.connection.commit()

class OpenDb(Screen):
    ''' Screen for selecting a database file '''
    BINDINGS = [Binding("escape", "app.pop_screen", "Pop screen")]

    class Fileopen(Message):
        ''' Message to notify that a database should be loaded '''
        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Label('Select Database to Open', id='openlabel')
        yield DirectoryTree(os.path.expanduser('~'), id='opentree')
        yield Button('Open', id='openbutton')

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ''' The Open button was pressed '''
        tree = self.query_one('#opentree', DirectoryTree)
        if tree.cursor_node and tree.cursor_node.data:
            self.post_message(self.Fileopen(tree.cursor_node.data.path))
            event.stop()


class DbTreeWidget(Tree):
    ''' Tree widget for showing DB tables '''
    def load_db(self, database: Database):
        ''' Load database data into the tree '''
        self.database = database
        self.clear()
        self.root.label = os.path.basename(database.path)
        self.root.expand()
        tree_tables = self.root.add("Tables", expand=True)
        for table in self.database.tables:
            tree_tables.add_leaf(table)
        tree_views = self.root.add("Views", expand=True)
        for view in self.database.views:
            tree_views.add_leaf(view)


class DbTableEdit(DataTable):
    ''' DataTable for showing and editing an SQLite table '''
    BINDINGS = [Binding('enter', 'edit_field', 'Edit Field'),
                Binding('i', 'insert_row', 'Insert Row')]

    @property
    def column_names(self) -> list[str]:
        ''' Get list of column names '''
        return [str(c.label) for c in self.ordered_columns]

    @property
    def current_column(self) -> str:
        ''' Get label for selected column '''
        return self.column_names[self.cursor_column]

    @property
    def current_value(self) -> str:
        ''' Get value of selected cell '''
        return self.get_cell_at(self.cursor_coordinate)

    def current_row_values(self, *columns: str) -> dict:
        ''' Get values of columns for selected row '''
        column_labels = tuple(self.column_names)
        if len(columns) == 0:
            columns = column_labels
        colids = [column_labels.index(c) for c in columns]
        col_values = [self.get_cell_at(Coordinate(self.cursor_row, i)) for i in colids]
        return dict(zip(columns, col_values))


class FieldEditor(Static):
    ''' Popup Widget for editing a single field in a table '''

    class ChangeField(Message):
        ''' Message to notify that the field should be changed '''
        def __init__(self, newvalue: str, changeinfo: TableEditInfo):
            self.newvalue = newvalue
            self.changeinfo = changeinfo
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Label('Edit me!', id='fieldname')
        yield Input(id='fieldinput')
        with Horizontal():
            yield Button('Commit', id='commit')
            yield Button('Cancel', id='cancel')

    def startedit(self, editinfo: TableEditInfo) -> None:
        ''' Start editing the field '''
        self.editinfo = editinfo
        self.styles.visibility = 'visible'
        fieldname = self.query_one('#fieldname', Label)
        fieldname.update(editinfo.column)
        value = self.query_one('#fieldinput', Input)
        value.action_end()
        value.action_delete_left_all()
        value.insert_text_at_cursor(editinfo.value)
        value.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ''' Enter was pressed in the Input. Commit the change. '''
        self.post_message(self.ChangeField(event.value, self.editinfo))
        self.styles.visibility = 'hidden'
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ''' A button was pressed. Commit the change or cancel. '''
        if event.button.id == 'cancel':
            self.styles.visibility = 'hidden'
        else:
            value = self.query_one('#fieldinput', Input).value
            self.post_message(self.ChangeField(value, self.editinfo))
            self.styles.visibility = 'hidden'

    def on_key(self, event: events.Key) -> None:
        ''' Key was pressed '''
        if event.key == 'escape':
            self.styles.visibility = 'hidden'


class InsertEditor(Static):
    ''' Screen for inserting an entire row into a table '''

    class InsertRow(Message):
        ''' Message to notify that a row should be inserted '''
        def __init__(self, tablename: str, values: dict[str, str]):
            self.values = values
            self.tablename = tablename
            super().__init__()

    class RowEdit(Static):
        ''' Label and Input Widgets '''
        def __init__(self, label: str, value: str):
            super().__init__()
            self.label = label
            self.initialvalue = value

        def compose(self) -> ComposeResult:
            with Horizontal():
                yield Label(self.label, id='roweditlabel')
                yield Input(self.initialvalue, id='roweditvalue')

        @property
        def value(self):
            ''' Get entered value as a string '''
            inpt = self.query_one('#value', Input)
            return str(inpt.value)

    def compose(self) -> ComposeResult:
        yield Label('Table Name', id='tablename')
        yield Container(id='widgetcontainer')
        with Horizontal():
            yield Button('Commit', id='commit')
            yield Button('Cancel', id='cancel')

    def clear(self) -> None:
        ''' Clear the widgets '''
        widgets = self.query(self.RowEdit)
        if widgets:
            widgets.remove()

    def startrow(self, tablename: str, columnnames: Sequence[str]) -> None:
        ''' Add widgets for entering a database row '''
        self.query_one('#tablename', Label).update(tablename)
        self.styles.visibility = 'visible'
        self.clear()
        for column in columnnames:
            widget = self.RowEdit(column, '')
            self.query_one('#widgetcontainer').mount(widget)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ''' A button was pressed. Commit the change or cancel. '''
        if event.button.id == 'cancel':
            self.styles.visibility = 'hidden'
        else:
            self.accept()
            self.styles.visibility = 'hidden'

    def on_key(self, event: events.Key) -> None:
        ''' Key was pressed '''
        if event.key == 'escape':
            self.styles.visibility = 'hidden'

    def accept(self):
        ''' Accept the new row '''
        tablename = str(self.query_one('#tablename', Label).renderable)
        values = {}
        for rowedit in self.query(self.RowEdit):
            key = str(rowedit.query_one('#roweditlabel', Label).renderable)
            value = str(rowedit.query_one('#roweditvalue', Input).value)
            if value:
                values[key] = value
        self.post_message(self.InsertRow(tablename, values))


class Sqint(App):
    ''' Main SQLite Viewer App '''
    CSS_PATH = 'sqint.css'
    SCREENS = {'opendb': OpenDb()}
    BINDINGS = [Binding("o", "push_screen('opendb')", "Open Database"),
                Binding("d", "toggle_dark", "Toggle dark mode")]

    def __init__(self, dbpath: str = None):
        super().__init__()
        self.database = Database()
        self.dbpath = dbpath
        self.currenttable: Optional[str] = None

    def on_mount(self) -> None:
        ''' Load the database when mounted '''
        if self.dbpath:
            self.load_database(self.dbpath)
        else:
            self.push_screen('opendb')

    def load_database(self, path: str) -> bool:
        ''' Load database info into widgets. Return True on success '''
        loaded = self.database.load(path)
        if loaded:
            self.query_one('#dbtree', DbTreeWidget).load_db(self.database)
        return loaded

    def compose(self) -> ComposeResult:
        yield Header()
        yield DbTreeWidget('database', id='dbtree')
        yield Tabs(
            Tab('Contents', id='dbtable_tab'),
            Tab('Table Info', id='infotable_tab'),
            Tab('Query', id='query_tab'))

        with ContentSwitcher(initial='dbtable'):
            yield DbTableEdit(id='dbtable')
            yield DataTable(id='infotable')
            with Vertical(id='query'):
                yield Input(placeholder='SELECT * FROM ?', id='queryinput')
                yield DataTable(id='queryoutput')
        yield InsertEditor(id='insertrow')
        yield FieldEditor(id='fieldedit')
        yield Footer()

    def on_tree_node_selected(self, message: Tree.NodeSelected) -> None:
        ''' Something was selected in the Database Tree '''
        if not message.node.allow_expand:
            self.currenttable = str(message.node.label)
            columns, rows = self.database.table_data(self.currenttable)
            table = self.query_one('#dbtable', DbTableEdit)
            table.clear(columns=True)
            table.add_columns(*columns)
            table.add_rows(rows)

            infotable = self.query_one('#infotable', DataTable)
            infotable.clear(columns=True)
            columns, info = self.database.table_info(str(message.node.label))
            infotable.add_columns(*columns)
            infotable.add_rows(info)

            contentswitcher = self.query_one(ContentSwitcher)
            if contentswitcher.current == 'query':
                contentswitcher.current = 'dbtable'

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        ''' Tab was changed '''
        if event.tab.id:
            tabid = event.tab.id.split('_')[0]
            try:
                self.query_one(ContentSwitcher).current = tabid
            except NoMatches:
                pass  # ContentSwitcher won't exist yet during initialization

    def action_edit_field(self) -> None:
        ''' Edit of the field was requested. Show edit popup. '''
        if self.currenttable and self.currenttable not in self.database.views:
            table = self.query_one('#dbtable', DbTableEdit)
            primary_keys = self.database.primary_keys(self.currenttable)
            conditions = table.current_row_values(*primary_keys)
            tableinfo = TableEditInfo(table.current_value,
                                      table.current_column,
                                      self.currenttable,
                                      conditions,
                                      table.cursor_coordinate)
            editbox = self.query_one('#fieldedit', FieldEditor)
            editbox.startedit(tableinfo)

    def action_insert_row(self) -> None:
        if self.currenttable and self.currenttable not in self.database.views:
            table = self.query_one('#dbtable', DbTableEdit)
            insertbox = self.query_one('#insertrow', InsertEditor)
            insertbox.startrow(self.currenttable, table.column_names)

    def on_field_editor_change_field(self, message: FieldEditor.ChangeField) -> None:
        ''' Field editor is done editing '''
        where = message.changeinfo.conditions
        table_name = message.changeinfo.tablename
        column_name = message.changeinfo.column
        coordinate = message.changeinfo.coordinate
        new_value = message.newvalue
        table = self.query_one('#dbtable', DbTableEdit)
        try:
            self.database.update(table_name, column_name, new_value, where)
        except sqlite3.Error:
            pass  # TODO: show error message
        else:
            table.update_cell_at(coordinate, new_value, update_width=True)
        table.focus()

    def on_insert_editor_insert_row(self, message: InsertEditor.InsertRow) -> None:
        ''' Insert Row editor has a row to insert '''
        try:
            self.database.insert(message.tablename, message.values)
        except sqlite3.Error:
            pass  # TODO: show error message
        else:
            if self.currenttable:
                columns, rows = self.database.table_data(self.currenttable)
                table = self.query_one('#dbtable', DbTableEdit)
                table.clear(columns=True)
                table.add_columns(*columns)
                table.add_rows(rows)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ''' The SQL query was submitted '''
        table = self.query_one('#queryoutput', DataTable)
        query = event.value
        table.clear(columns=True)
        try:
            columns, result = self.database.query(query)
        except sqlite3.OperationalError as err:
            columns = ['Error',]
            result = [[str(err)]]

        table.add_columns(*columns)
        table.add_rows(result)

    def action_toggle_dark(self) -> None:
        ''' Dark mode '''
        self.dark = not self.dark

    def on_open_db_fileopen(self, message: OpenDb.Fileopen) -> None:
        ''' OpenDb wants to open a database to load '''
        self.pop_screen()
        if not self.load_database(message.path):
            self.push_screen('opendb')


def main():
    if len(sys.argv) <= 1:
        dbpath = None
    else:
        dbpath = sys.argv[1]

    app = Sqint(dbpath)
    app.run()


if __name__ == "__main__":
    main()
