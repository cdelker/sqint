''' Textual viewer/editor for SQLite databases '''

import os
import sys
import sqlite3
from typing import Sequence

from textual import events
from textual.app import App, ComposeResult, NoMatches, Binding
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
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.coordinate import Coordinate


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
        try:
            cursor = self.connection.execute(query, args)
        except AttributeError:
            columns: list[str] = []
            rows: list[list[str]] = [[]]
        else:
            columns = [col[0] for col in cursor.description]
            rows = [[str(col) for col in row] for row in cursor.fetchall()]
        return columns, rows

    def table_info(self, name: str) -> tuple[list[str], list[list[str]]]:
        ''' Get table info '''
        columns, info = self.query(f'PRAGMA table_info({name});')
        return columns, info

    def table_data(self, name: str) -> tuple[list[str], list[list[str]]]:
        ''' Get column names and row data from table '''
        columns, rows = self.query(f'SELECT * FROM {name}')
        return columns, rows

    def primary_keys(self, name: str) -> list[str]:
        ''' Get primary key columns for a table '''
        _, rows = self.query(
            f'SELECT l.name FROM pragma_table_info("{name}") as l WHERE l.pk = 1;')
        return [r[0] for r in rows]

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
        with Horizontal():
            yield Button('Open', id='openbutton')
            yield Label('Not a database', id='notadatabase')

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

    class StartEdit(Message):
        ''' Message sent when a field edit was requested '''
        def __init__(self,
                     table_name: str,
                     column_name: str,
                     coordinate: Coordinate,
                     current_value: str,
                     where: dict) -> None:
            self.table_name = table_name
            self.column_name = column_name
            self.coordinate = coordinate
            self.current_value = current_value
            self.where = where
            super().__init__()

    def fill_table(self,
                   tablename: str,
                   primary_keys: Sequence[str],
                   columns: Sequence[str],
                   rows: Sequence[Sequence[str]]) -> None:
        ''' Populate the table with data '''
        self.tablename = tablename
        self.primary_keys = primary_keys
        self.clear(columns=True)
        self.add_columns(*columns)
        self.add_rows(rows)

    def on_key(self, event: events.Key) -> None:
        ''' A key was pressed. '''
        if event.key == 'enter':
            coordinate = Coordinate(self.cursor_row, self.cursor_column)
            currentvalue = self.get_cell_at(coordinate)

            column_labels = [str(c.label) for c in self.ordered_columns]
            selectedcol = column_labels[self.cursor_column]

            primary_key_ids = [column_labels.index(pk) for pk in self.primary_keys]
            primary_key_values = [
                self.get_cell_at(Coordinate(self.cursor_row, i)) for i in primary_key_ids]
            conditions = dict(zip(self.primary_keys, primary_key_values))
            self.post_message(
                self.StartEdit(self.tablename, selectedcol, coordinate, currentvalue, conditions))


class FieldEditor(Static):
    ''' Popup Widget for editing a single field in a table '''

    class ChangeField(Message):
        ''' Message to notify that the field should be changed '''
        def __init__(self, new_value: str, table_info: DbTableEdit.StartEdit):
            self.new_value = new_value
            self.table_info = table_info
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Label('Edit me!', id='fieldname')
        yield Input(id='fieldinput')
        with Horizontal():
            yield Button('Commit', id='commit')
            yield Button('Cancel', id='cancel')

    def startedit(self, message: DbTableEdit.StartEdit) -> None:
        ''' Start editing the field '''
        self.table_info = message
        self.styles.visibility = 'visible'
        fieldname = self.query_one('#fieldname', Label)
        fieldname.update(message.column_name)
        value = self.query_one('#fieldinput', Input)
        value.action_end()
        value.action_delete_left_all()
        value.insert_text_at_cursor(message.current_value)
        value.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ''' Enter was pressed in the Input. Commit the change. '''
        self.post_message(self.ChangeField(event.value, self.table_info))
        self.styles.visibility = 'hidden'
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ''' A button was pressed. Commit the change or cancel. '''
        if event.button.id == 'cancel':
            self.styles.visibility = 'hidden'
        else:
            value = self.query_one('#fieldinput', Input)
            self.post_message(self.ChangeField(value.value, self.table_info))
            self.styles.visibility = 'hidden'

    def on_key(self, event: events.Key) -> None:
        ''' Key was pressed '''
        if event.key == 'escape':
            self.styles.visibility = 'hidden'


class SqliteViewer(App):
    ''' Main SQLite Viewer App '''
    CSS_PATH = 'sqliteview.css'
    SCREENS = {"opendb": OpenDb()}
    BINDINGS = [Binding("o", "push_screen('opendb')", "Open Database"),
                Binding("d", "toggle_dark", "Toggle dark mode")]

    def __init__(self, dbpath: str = None):
        super().__init__()
        self.database = Database()
        self.dbpath = dbpath

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
        yield FieldEditor(id='fieldedit')
        yield Footer()

    def on_tree_node_selected(self, message: Tree.NodeSelected) -> None:
        ''' Something was selected in the Database Tree '''
        if not message.node.allow_expand:
            tablename = str(message.node.label)
            columns, rows = self.database.table_data(tablename)
            table = self.query_one('#dbtable', DbTableEdit)
            table.fill_table(tablename, self.database.primary_keys(tablename), columns, rows)

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

    def on_db_table_edit_start_edit(self, message: DbTableEdit.StartEdit) -> None:
        ''' Database Table wants to start editing a field '''
        editbox = self.query_one('#fieldedit', FieldEditor)
        editbox.startedit(message)

    def on_field_editor_change_field(self, message: FieldEditor.ChangeField) -> None:
        ''' Field editor is done editing '''
        where = message.table_info.where
        table_name = message.table_info.table_name
        column_name = message.table_info.column_name
        coordinate = message.table_info.coordinate
        new_value = message.new_value
        table = self.query_one('#dbtable', DbTableEdit)
        table.update_cell_at(coordinate, new_value)
        table.focus()
        self.database.update(table_name, column_name, new_value, where)

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


if __name__ == "__main__":
    if len(sys.argv) <= 1:
        dbpath = None
    else:
        dbpath = sys.argv[1]

    app = SqliteViewer(dbpath)
    app.run()
