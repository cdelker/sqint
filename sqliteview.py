''' Textual viewer for SQLite databases '''

import os
import sys
import sqlite3

from textual.app import App, ComposeResult, NoMatches
from textual.widgets import DirectoryTree, DataTable, Input, Button, Tree, Header, Label, Footer, ContentSwitcher, Tab, Tabs
from textual.screen import Screen
from textual.containers import Horizontal, Vertical
from textual.message import Message


class Database:
    ''' The Sqlite Database '''
    def __init__(self, path):
        self.path = path
        self.name = os.path.split(self.path)[1]
        self.connection = sqlite3.connect(self.path)
        _, tables = self.query(
            "SELECT name FROM sqlite_schema WHERE type='table'")
        self.tables = [t[0] for t in tables]
        _, views = self.query(
            "SELECT name FROM sqlite_schema WHERE type='view'")
        self.views = [v[0] for v in views]

    def query(self, query, args=None):
        ''' Query the database '''
        args = () if args is None else args
        cursor = self.connection.execute(query, args)
        columns = [col[0] for col in cursor.description]
        rows = [[str(col) for col in row] for row in cursor.fetchall()]
        return columns, rows

    def table_info(self, name):
        ''' Get table info '''
        columns, info = self.query(f'PRAGMA table_info({name});')
        return columns, info

    def table_data(self, name):
        ''' Get column names and row data from table '''
        columns, rows = self.query(f'SELECT * FROM {name}')
        return columns, rows


class OpenDb(Screen):
    ''' Screen for selecting a database file '''
    BINDINGS = [("escape", "app.pop_screen", "Pop screen")]

    class Fileopen(Message):
        def __init__(self, db: Database) -> None:
            self.database = db
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Label('Select Database to Open', id='openlabel')
        yield DirectoryTree(os.path.expanduser('~'), id='opentree')
        with Horizontal():
            yield Button('Open', id='openbutton')
            yield Label('Not a database', id='notadatabase')

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ''' The Open button was pressed '''
        tree = self.query_one('#opentree')
        try:
            database = Database(tree.cursor_node.data.path)
        except sqlite3.DatabaseError:
            self.query_one('#notadatabase').styles.visibility = 'visible'
        else:
            self.post_message(self.Fileopen(database))
        event.stop()


class DbTreeWidget(Tree):
    ''' Tree widget for showing DB tables '''
    def load_db(self, database):
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


class SqliteViewer(App):
    ''' Main SQLite Viewer App '''
    CSS_PATH = 'sqliteview.css'
    SCREENS = {"opendb": OpenDb()}
    BINDINGS = [("o", "push_screen('opendb')", "Open Database"),
                ("d", "toggle_dark", "Toggle dark mode")]

    def __init__(self, dbpath=None):
        super().__init__()
        self.database = None
        self.dbpath = dbpath

    def on_mount(self) -> None:
        if self.dbpath:
            self.load_database(Database(self.dbpath))
        else:
            self.push_screen('opendb')

    def load_database(self, database):
        ''' Load database info into widgets '''
        self.database = database
        self.query_one('#dbtree').load_db(database)

    def compose(self) -> ComposeResult:
        yield Header()
        yield DbTreeWidget('database', id='dbtree')
        yield Tabs(
            Tab('Contents', id='dbtable_tab'),
            Tab('Table Info', id='infotable_tab'),
            Tab('Query', id='query_tab')
        )

        with ContentSwitcher(initial='dbtable'):
            yield DataTable(id='dbtable')
            yield DataTable(id='infotable')
            with Vertical(id='query'):
                yield Input(placeholder='SELECT * FROM ?', id='queryinput')
                yield DataTable(id='queryoutput')

        yield Footer()

    def on_tree_node_selected(self, message):
        ''' Something was selected in the Database Tree '''
        if not message.node.allow_expand:
            columns, rows = self.database.table_data(message.node.label)
            table = self.query_one('#dbtable', DataTable)
            table.clear(columns=True)
            table.add_columns(*columns)
            table.add_rows(rows)

            infotable = self.query_one('#infotable', DataTable)
            infotable.clear(columns=True)
            columns, info = self.database.table_info(message.node.label)
            infotable.add_columns(*columns)
            infotable.add_rows(info)

            contentswitcher = self.query_one(ContentSwitcher)
            if contentswitcher.current == 'query':
                contentswitcher.current = 'dbtable'

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        ''' Tab was changed '''
        tabid = event.tab.id.split('_')[0]
        try:
            self.query_one(ContentSwitcher).current = tabid
        except NoMatches:
            pass  # ContentSwitcher won't exist yet during initialization

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ''' The SQL query was submitted '''
        table = self.query_one('#queryoutput')
        query = event.value
        table.clear(columns=True)
        try:
            columns, result = self.database.query(query)
        except sqlite3.OperationalError as err:
            columns = ['Error']
            result = ((str(err),),)

        table.add_columns(*columns)
        table.add_rows(result)

    def action_toggle_dark(self) -> None:
        ''' Dark mode '''
        self.dark = not self.dark

    def on_open_db_fileopen(self, message):
        ''' Received message from OpenDb with database to load '''
        self.pop_screen()
        self.load_database(message.database)


if __name__ == "__main__":
    if len(sys.argv) <= 1:
        dbpath = None
    else:
        dbpath = sys.argv[1]

    app = SqliteViewer(dbpath)
    app.run()
