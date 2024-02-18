import steamapi
import pyperclip
from steamapi.errors import AccessException
from steamapi.user import UserNotFoundError
import toga
from toga.style.pack import COLUMN, Pack, ROW, CENTER, MONOSPACE

class Result:
    def __init__(self, api_key, steam_ids, error_handler):
        self.error_handler = error_handler
        self.connect_to_steam(api_key, steam_ids)
        self.get_common_games()
        self.display_results()


    def connect_to_steam(self, api_key, steam_ids):
        self.connection = steamapi.core.APIConnection(api_key=api_key, validate_key=True)
        self.users: list[steamapi.user.SteamUser] = []

        for steam_id in steam_ids:
            try:
                user = steamapi.user.SteamUser(steam_id)
                self.users.append(user)
                name = user.name
                continue
            except UserNotFoundError:
                self.error_handler("User not found", "User with id \'" + id + "\' was not found.\n\nYou may have supplied an invalid steam_id. Please make sure you are using the correct decimal 64-bit id.\nWill continue without this id.")

    def get_common_games(self):
        all_games = []
        all_gameids = []
        all_users_gameids = []

        print("\n\n*STEAM USERS*")

        restricted_privacy_users: list[str] = []
        unrestricted_privacy_users: list[str] = []

        for user in self.users:
            user_game_ids = []
            try:
                for game in user.games:
                    appid = game.appid
                    if appid not in all_gameids:
                        all_games.append(game)
                        all_gameids.append(appid)
                    user_game_ids.append(game.appid)
                all_users_gameids.append(user_game_ids)
                print(user.name + ", level " + str(user.level) + ", owns " + str(len(user_game_ids)) + " games")
                unrestricted_privacy_users.append(user.name)
            except AccessException:
                restricted_privacy_users.append(user.name)

        if len(all_users_gameids) == 0:
            self.error_handler("Not enough players", "Could not find any players with unrestricted privacy settings.")
            return

        # self.error_handler("Steam API Error", "Restricted privacy settings to view library for user \'" + user.name + "\'")

        if len(restricted_privacy_users) > 0:
            self.error_handler("Partial success", "Found " + str(len(restricted_privacy_users)) + " players with restricted privacy settings.\nPlayers with private libraries:\n\n" + ", ".join(restricted_privacy_users) + " \n\nPlayers with public libraries:\n" + ", ".join(unrestricted_privacy_users))

        common_game_ids = all_users_gameids[0]
        for i in range(1, len(all_users_gameids)):
            common_game_ids = self.intersection(common_game_ids, all_users_gameids[i])

        common_games = [self.get_game_from_list(all_games, steam_id) for steam_id in common_game_ids]
        common_games = list(filter(None, common_games))

        self.common_game_names = [game.name for game in common_games]
        self.common_game_names.sort()

        print("\n\n*COMMON GAMES*")
        for game_name in self.common_game_names:
            print(game_name)

    def intersection(self, list_a, list_b):
        return list(set(list_a) & set(list_b))

    def get_game_from_list(self, games, appid):
        for game in games:
            if appid == game.appid:
                return game
        return None

    def display_results(self):
        game_names_str = "\n".join(self.common_game_names)

        data = []
        for game_name in self.common_game_names:
            data.append((str(len(data) + 1), game_name))

        window_content = toga.Box(style=Pack(direction=COLUMN, alignment=CENTER))
        window_content.add(toga.Table(headings=["Index", "Game"], multiple_select=True, data=data, style=Pack(height=250)))
        window_content.add(toga.Button("Copy to clipboard", on_press=self.copy_games,
                                       style=Pack(width=300, padding=10, height=30)))

        result_window = toga.Window(title="Common Games", size=(500, 300))
        result_window.position = (400, 300)
        result_window.content = window_content
        result_window.show()

    def copy_games(self, button):
        names = ""

        for game in self.common_game_names:
            names += game + "\n"

        pyperclip.copy(names)


class SteamUserRow(toga.Box):
    def __init__(self, index):
        super().__init__(style=Pack(direction=ROW, padding=10))
        self.index = index
        self.steam_id: str = None

        self.add(toga.Label(str(self.index) + ')', style=Pack(font_family=MONOSPACE, font_size=12)))

        self.add(toga.Label("Steam Id:", style=Pack(padding_left=15)))
        steam_id_input = toga.TextInput(style=Pack(width=300))
        steam_id_input.on_change = self.on_steam_id_input_change
        self.add(steam_id_input)

    def on_steam_id_input_change(self, text_input: toga.TextInput):
        # Need to verify this.
        self.steam_id = text_input.value


class GameFinder(toga.App):
    def __init__(self):
        super().__init__("Game Finder", "steam_friends_game_finder")

    def startup(self) -> None:
        self.main_window = toga.MainWindow(size=(1280, 720))
        self.configure_toolbar()

        self.content_right = toga.Box(style=Pack(direction=COLUMN, padding=5))
        self.steam_user_rows: list[SteamUserRow] = []
        self.steam_api_input: toga.TextInput = None



        content_top = toga.SplitContainer()
        content_top.content = [(self.create_content_left(), 1), (self.create_content_right(), 2)]

        content_bottom = toga.Box(style=Pack(direction=COLUMN, padding=10, alignment=CENTER, height=100))
        content_bottom.add(toga.Button("Submit", on_press=self.submit, style=Pack(width=300)))

        content_base = toga.SplitContainer(direction=toga.SplitContainer.HORIZONTAL)
        content_base.content = [(content_top, 10), (content_bottom, 1)]

        self.main_window.content = content_base
        self.main_window.show()

    def submit(self, button):
        steam_ids: list[str] = []

        for row in self.steam_user_rows:
            if row.steam_id is None or row.steam_id is '':
                continue
            if not self.is_valid_steam_id(row.steam_id):
                self.show_error(
                    "Input Error",
                    "steam_id \'" + row.steam_id + "\' at row " + str(row.index) + " is not a 17 digits number!"
                )
                return
            steam_ids.append(row.steam_id)

        if len(steam_ids) < 2:
            self.show_error("Input Error", "You need to enter at least 2 Steam Ids or Steam Profile Urls.")
            return

        if self.steam_api_input.value is None or self.steam_api_input.value is '':
            self.show_error(
                "Input Error",
                "You need to enter your API key. You can get it here: https://steamcommunity.com/dev/apikey"
            )
            return

        if not self.is_valid_api_key(self.steam_api_input.value):
            self.show_error(
                "Input Error",
                "You have entered an invalid API key. The key contains of 32 characters."
            )
            return

        print("Submitting!")
        result = Result(self.steam_api_input.value, steam_ids, self.show_error)


    def is_valid_steam_id(self, steam_id: str):
        return len(steam_id) == 17 and steam_id.isdigit()

    def is_valid_api_key(self, api_key: str):
        return len(api_key) == 32

    def show_error(self, title: str, message: str):
        error_window = toga.Window(title=title, size=(500, 150))
        error_window.position = (400, 300)
        error_window.content = toga.Box(children=[toga.Label(text=message)])
        error_window.show()

    def test_command(self, command):
        print("test_command")

    def configure_toolbar(self):
        toolbar_actions = toga.Group("toolbar_actions")

        self.main_window.toolbar.add(toga.Command(
            self.test_command,
            text="Test Command Action 1",
            tooltip="Perform test action 1",
            # icon=,
            group=toolbar_actions,
        ))
        self.main_window.toolbar.add(toga.Command(
            self.test_command,
            text="Test Command Action 2",
            tooltip="Perform test action 2",
            # icon=,
            group=toolbar_actions,
        ))

    def create_content_left(self):
        container = toga.Box(style=Pack(
            direction=COLUMN, padding_top=30, padding_left=20, padding_right=20, padding_bottom=30
        ))

        steam_api_label = toga.Label("Steam API Key")
        container.add(steam_api_label)

        self.steam_api_input = toga.TextInput(style=Pack(padding_top=5))
        container.add(self.steam_api_input)

        content_left = toga.ScrollContainer(horizontal=False)
        content_left.content = container
        return content_left

    def add_steam_user_row(self, button):
        row = SteamUserRow(len(self.steam_user_rows) + 1)
        self.steam_user_rows.append(row)
        self.content_right.add(row)

    def remove_steam_user_row(self, button):
        if len(self.steam_user_rows) <= 2:
            return

        self.content_right.remove(self.steam_user_rows.pop())

    def create_content_right(self):
        add_button = toga.Button("Add", on_press=self.add_steam_user_row, style=Pack(padding=10, width=130))
        remove_button = toga.Button("Remove", on_press=self.remove_steam_user_row, style=Pack(padding=10, width=130))

        button_container = toga.Box(style=Pack(direction=ROW, padding=5))
        self.content_right.add(toga.Label("Add either a steam_id or a Steam Profile Url.", style=Pack(padding_left=10, font_size=12, padding_top=15)))
        button_container.add(add_button)
        button_container.add(remove_button)
        self.content_right.add(button_container)
        self.content_right.add(toga.Divider(style=Pack(padding=5)))

        # Add initial rows
        self.add_steam_user_row(add_button)
        self.add_steam_user_row(add_button)

        return self.content_right


GameFinder().main_loop()
