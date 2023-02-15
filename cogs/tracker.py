import asyncio
import time
from typing import Dict, List, Union
import aiohttp

import nextcord
import undetected_chromedriver as uc
from nextcord.ext import commands, tasks
from selenium.webdriver.common.by import By

from internal_tools.configuration import CONFIG, JsonDictSaver
from internal_tools.discord import *


class Tracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.collection_events = JsonDictSaver(
            "collection_events", auto_convert_data=False
        )
        self.event_log_listeners = JsonDictSaver("event_log_listeners")

        self.update_data.start()

    async def cog_application_command_check(self, interaction: nextcord.Interaction):
        """
        Everyone can use this.
        """
        return True

    def compare_events(
        self, event1: Dict[str, Union[str, None]], event2: Dict[str, Union[str, None]]
    ):
        if event1["EVENT_TYPE"] != event2["EVENT_TYPE"]:
            return False
        elif event1["TOKEN_ID"] != event2["TOKEN_ID"]:
            return False
        elif event1["PRICE"] != event2["PRICE"]:
            return False
        elif event1["TO_ADDRESS"] != event2["TO_ADDRESS"]:
            return False
        elif event1["FROM_ADDRESS"] != event2["FROM_ADDRESS"]:
            return False

        return True

    def _scrape_data(
        self, url: str, known_entries: List[Dict[str, Union[str, None]]] = []
    ):
        new_data = []

        driver = uc.Chrome(browser_executable_path="brave-browser")

        driver.get(url)
        time.sleep(2)

        driver.find_element(
            By.XPATH, CONFIG["ALTO_TRACKER"]["SELECTORS"]["ACTIVITY_TAB"]
        ).click()
        time.sleep(3)

        table = driver.find_element(
            By.XPATH, CONFIG["ALTO_TRACKER"]["SELECTORS"]["ACTIVITY_TABLE"]
        )
        for entry in reversed(table.find_elements(By.XPATH, "./*")):
            entry_data_raw = entry.find_elements(By.XPATH, "./*")
            entry_data = {}

            entry_data["EVENT_TYPE"] = entry_data_raw[0].text

            try:
                entry_data["PREVIEW_IMAGE_URL"] = (
                    entry_data_raw[1]
                    .find_element(By.XPATH, ".//img")
                    .get_attribute("src")
                )
            except:
                entry_data["PREVIEW_IMAGE_URL"] = None

            entry_data["TOKEN_ID"] = entry_data_raw[1].text

            entry_data["TOKEN_URL"] = url + "/" + str(entry_data["TOKEN_ID"])

            if entry_data_raw[2].text != "--":
                entry_data["PRICE"] = entry_data_raw[2].text.replace("\nCANTO", "")
            else:
                entry_data["PRICE"] = None

            if entry_data_raw[3].text != "--":
                entry_data["TO_ADDRESS_URL"] = (
                    entry_data_raw[3]
                    .find_element(By.XPATH, "./a")
                    .get_attribute("href")
                )
                entry_data["TO_ADDRESS"] = entry_data["TO_ADDRESS_URL"].rsplit("/", 1)[
                    1
                ]
            else:
                entry_data["TO_ADDRESS"] = None
                entry_data["TO_ADDRESS_URL"] = None

            if (
                entry_data_raw[4].text != "--"
                and entry_data_raw[4].text != "null address"
            ):
                entry_data["FROM_ADDRESS_URL"] = (
                    entry_data_raw[4]
                    .find_element(By.XPATH, "./a")
                    .get_attribute("href")
                )
                entry_data["FROM_ADDRESS"] = entry_data["FROM_ADDRESS_URL"].rsplit(
                    "/", 1
                )[1]
            else:
                entry_data["FROM_ADDRESS"] = None
                entry_data["FROM_ADDRESS_URL"] = None

            for known_entry in known_entries:
                if self.compare_events(entry_data, known_entry):
                    break
            else:
                new_data.append(entry_data)

        try:
            driver.close()
        except:
            pass

        try:
            driver.quit()
        except:
            pass

        return new_data

    async def get_new_events(
        self,
        collection_name: str,
        known_events: List[Dict[str, Union[str, None]]] = [],
    ):
        loop = asyncio.get_running_loop()
        try:
            new_events = await loop.run_in_executor(
                None,
                self._scrape_data,
                CONFIG["ALTO_TRACKER"]["MARKETPLACE_BASE_URL"] + "/" + collection_name,
                known_events,
            )
        except:
            new_events = []

        return new_events

    async def log_events(
        self,
        collection_name: str,
        events: List[Dict[str, Union[str, None]]],
    ):
        for _, webhook_url in self.event_log_listeners[collection_name].items():
            async with aiohttp.ClientSession() as session:
                try:
                    webhook = nextcord.Webhook.from_url(webhook_url, session=session)
                except:
                    continue

                for event in events:
                    fields = {}

                    if event["PRICE"] != None:
                        fields["Price"] = f"{event['PRICE']} CANTO"

                    if event["FROM_ADDRESS"] != None:
                        fields[
                            "From Address"
                        ] = f"[{event['FROM_ADDRESS']}]({event['FROM_ADDRESS_URL']})"

                    if event["TO_ADDRESS"] != None:
                        fields[
                            "To Address"
                        ] = f"[{event['TO_ADDRESS']}]({event['TO_ADDRESS_URL']})"

                    fields["Alto URL to Token"] = f"[Link]({event['TOKEN_URL']})"

                    embed = fancy_embed(
                        title=str(event["EVENT_TYPE"]),
                        fields=fields,
                        thumbnail_url=event["PREVIEW_IMAGE_URL"],
                    )
                    await webhook.send(embed=embed, username=self.bot.user.name, avatar_url=self.bot.user.avatar.url)  # type: ignore

    @tasks.loop(minutes=CONFIG["ALTO_TRACKER"]["UPDATE_LOOP_MINUTES"])
    async def update_data(self):
        for collection_name in self.event_log_listeners.copy():
            known_events = self.collection_events[collection_name].copy()

            new_events = await self.get_new_events(collection_name, known_events)

            try:
                await self.log_events(collection_name, new_events)
            except:
                pass

            known_events.extend(new_events)

            self.collection_events[collection_name] = known_events
            self.collection_events.save()

    @nextcord.slash_command(
        "add-collection",
        description="Add a collection the Bot should track.",
        dm_permission=False,
        default_member_permissions=nextcord.Permissions(manage_messages=True),
    )
    async def add_collection(
        self,
        interaction: nextcord.Interaction,
        collection_link: str = nextcord.SlashOption(
            name="collection-link",
            description="The link to the collection to track on Alto.",
        ),
        webhook_url: str = nextcord.SlashOption(
            name="webhook-url", description="URL of the Webhook to use for Alto Events."
        ),
    ):
        if interaction.guild_id not in CONFIG["ALTO_TRACKER"]["ALLOWED_GUILD_IDS"]:
            await interaction.send(
                "You have not paid for this Service.\nSend my Creator a Message and make a deal with her.\n\nHer Discord: Aki ToasterUwU#0001"
            )
            return

        await interaction.response.defer()

        collection_name = collection_link.rsplit("/", 1)[1]

        try:
            initial_events = await self.get_new_events(collection_name)
        except:
            await interaction.send("You provided an invalid link for the collection.")
            return

        async with aiohttp.ClientSession() as session:
            try:
                webhook = nextcord.Webhook.from_url(webhook_url, session=session)
                await webhook.send(
                    embed=fancy_embed("Testing", description="Testing the Webhook"), username=self.bot.user.name, avatar_url=self.bot.user.display_avatar.url  # type: ignore
                )
            except:
                await interaction.send("You provided an invalid Webhook URL.")
                return

        if collection_name not in self.event_log_listeners:
            self.event_log_listeners[collection_name] = {}
            self.event_log_listeners.save()

        self.event_log_listeners[collection_name][interaction.guild_id] = webhook_url
        self.event_log_listeners.save()

        self.collection_events[collection_name] = initial_events
        self.collection_events.save()

        await interaction.send(f"Logger is set up for: {collection_link}")

    @nextcord.slash_command(
        "remove-collection",
        description="Stop tracking a collection.",
        dm_permission=False,
        default_member_permissions=nextcord.Permissions(manage_messages=True),
    )
    async def remove_collection(
        self,
        interaction: nextcord.Interaction,
        collection_link: str = nextcord.SlashOption(
            name="collection-link",
            description="The link to the collection on Alto.",
        ),
    ):
        collection_name = collection_link.rsplit("/", 1)[1]

        if collection_name not in self.event_log_listeners:
            await interaction.send("You arent tracking this collection anyways.")
            return

        del self.event_log_listeners[collection_name]
        if self.event_log_listeners[collection_name] == {}:
            del self.event_log_listeners[collection_name]

        self.event_log_listeners.save()

        await interaction.send("You wont get messages about this Collection anymore.")

    @nextcord.slash_command(
        name="add-allowed-guild",
        guild_ids=CONFIG["GENERAL"]["OWNER_COG_GUILD_IDS"],
    )
    async def add_allowed_guild(self, interaction: nextcord.Interaction, guild_id: str):
        await interaction.response.defer()

        guild_id = int(guild_id)  # type: ignore

        if guild_id in CONFIG["ALTO_TRACKER"]["ALLOWED_GUILD_IDS"]:
            await interaction.send("This Guild already is allowed")
            return

        CONFIG["ALTO_TRACKER"]["ALLOWED_GUILD_IDS"].append(guild_id)
        CONFIG.save()

        await interaction.send("Done, added this Guild to the allow list.")

    @nextcord.slash_command(
        name="remove-allowed-guild",
        guild_ids=CONFIG["GENERAL"]["OWNER_COG_GUILD_IDS"],
    )
    async def remove_allowed_guild(
        self, interaction: nextcord.Interaction, guild_id: str
    ):
        await interaction.response.defer()

        guild_id = int(guild_id)  # type: ignore

        if guild_id not in CONFIG["ALTO_TRACKER"]["ALLOWED_GUILD_IDS"]:
            await interaction.send("This Guild isnt allowed anyways")
            return

        CONFIG["ALTO_TRACKER"]["ALLOWED_GUILD_IDS"].remove(guild_id)
        CONFIG.save()

        await interaction.send("Done, removed this Guild from the allow list.")


async def setup(bot):
    bot.add_cog(Tracker(bot))
