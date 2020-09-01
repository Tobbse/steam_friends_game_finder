import tkinter as tk
import steamapi


class Application(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.create_widgets(master)


    def create_widgets(self, master):
        tk.Label(master, text=" ").grid(row=0, column=0)

        tk.Label(master, text="Steam API Key:").grid(row=2, column=0)
        self.api_key = tk.Entry(master, borderwidth=1)
        self.api_key.grid(row=2, column=1)

        tk.Label(master, text=" ").grid(row=3, column=0)
        tk.Label(master, text="Add SteamIds to compare\n(Seperate with commas):").grid(row=4, column=0, ipady=30)

        steamids_scrollbar = tk.Scrollbar(master)
        self.steamids_input = tk.Text(master, height=5, width=30, borderwidth=2, relief="solid")
        steamids_scrollbar.config(command=self.steamids_input.yview)
        self.steamids_input.config(yscrollcommand=steamids_scrollbar.set, height=3)
        self.steamids_input.insert(tk.END, "Add friends here.")
        self.steamids_input.grid(row=4, column=1)
        steamids_scrollbar.grid(row=4, column=2)

        tk.Label(master, text=" ").grid(row=5, column=0)

        submit = tk.Button(master)
        submit["text"] = "Show Common Games"
        submit["command"] = self.submit
        submit.grid(row=6, column=1)

        reset = tk.Button(master)
        reset["text"] = "Reset Data"
        reset["command"] = self.reset
        reset.grid(row=6, column=0)


    def submit(self):
        api_key = self.api_key.get()
        steamids = self.get_steamids(self.steamids_input.get("1.0", tk.END))

        if not self.is_valid_api_key(api_key):
            print("Invalid API key!")
            print(api_key)
            self.reset()
            return

        if len(steamids) < 2:
            print("Invalid friends array! At least two valid SteamIds needed.")
            print(steamids)
            self.reset()
            return

        for steamid in steamids:
            if not self.is_valid_steamid(steamid):
                print("SteamID " + steamid + " is not a 17 digits number!")
                self.reset()
                return

        print("Entered SteamIds:")
        print(steamids)

        new_root = tk.Tk()
        new_root.geometry("360x560")
        Result(new_root, api_key, steamids)


    def reset(self):
        self.api_key.delete(0, tk.END)
        self.steamids_input.delete(0.0, tk.END) # Yes, this actually can't be an int. I have no idea why.


    def get_steamids(self, steamids_str):
        steamids_str = "".join(steamids_str.split())
        return steamids_str.split(',')


    def is_valid_steamid(self, steamid):
        return len(steamid) == 17 and steamid.isdigit()


    def is_valid_api_key(self, api_key):
        return len(api_key) == 32




class Result(tk.Frame):
    def __init__(self, master, api_key, steamids):
        super().__init__(master)
        self.master = master
        self.connect_to_steam(api_key, steamids)
        self.get_common_games()
        self.display_results()


    def connect_to_steam(self, api_key, steamids):
        self.connection = steamapi.core.APIConnection(api_key=api_key, validate_key=True)
        self.users = [steamapi.user.SteamUser(id) for id in steamids]


    def get_common_games(self):
        all_games = []
        all_gameids = []
        all_users_gameids = []

        print("\n\n*STEAM USERS*")

        for user in self.users:
            user_game_ids = []
            for game in user.games:
                appid = game.appid
                if appid not in all_gameids:
                    all_games.append(game)
                    all_gameids.append(appid)
                user_game_ids.append(game.appid)
            all_users_gameids.append(user_game_ids)
            print(user.name + ", level " + str(user.level) + ", owns " + str(len(user_game_ids)) + " games")

        common_gameids = all_users_gameids[0]
        for i in range(1, len(all_users_gameids)):
            common_gameids = self.intersection(common_gameids, all_users_gameids[i])

        common_games = [self.get_game_from_list(all_games, steamid) for steamid in common_gameids]
        common_games = list(filter(None, common_games))

        self.common_game_names = [game.name for game in common_games]
        self.common_game_names.sort()

        print("\n\n*COMMON GAMES*")
        for game_name in self.common_game_names:
            print(game_name)


    def display_results(self):
        game_names_str = "\n".join(self.common_game_names)
        game_names_scrollbar = tk.Scrollbar(self.master)
        game_names_display = tk.Text(self.master, height=33, width=40, borderwidth=2, relief="solid")
        game_names_scrollbar.config(command=game_names_display.yview)
        game_names_display.config(yscrollcommand=game_names_scrollbar.set, height=33, width=40)
        game_names_display.insert(tk.END, game_names_str)
        game_names_display.grid(row=0, column=0)
        game_names_scrollbar.grid(row=0, column=1)


    def intersection(self, list_a, list_b): 
        return list(set(list_a) & set(list_b))


    def get_game_from_list(self, games, appid):
        for game in games:
            if appid == game.appid:
                return game
        return None




root = tk.Tk()
root.geometry("420x270")
app = Application(master=root)
app.mainloop()
