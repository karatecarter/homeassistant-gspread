import logging
from aiohttp import ClientError
import gspread

# import pandas as pd
from homeassistant import config_entries, core
from oauth2client.service_account import ServiceAccountCredentials
from homeassistant.helpers.entity_platform import current_platform
from datetime import timedelta
from typing import Any, Optional
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import (
    HomeAssistantType,
)
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
# Time between updating data from Google
SCAN_INTERVAL = timedelta(minutes=1)

CONF_API_KEY = "api_key"


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Setup sensors from a config entry created in the integrations UI."""
    config = hass.data[DOMAIN][config_entry.entry_id]

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    api_key_dict = config[CONF_API_KEY]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(api_key_dict, scope)

    sensors = [GspreadSensor(hass, creds, config["sheet_name"])]
    async_add_entities(sensors, update_before_add=True)

    platform = current_platform.get()
    platform.async_register_entity_service(
        "log",
        {
            vol.Required("date"): cv.string,
            vol.Required("period", default="morning"): cv.string,
            vol.Required("amount"): cv.positive_float,
        },
        "log_to_gspread",
    )
    platform.async_register_entity_service(
        "clear",
        {},
        "clear_gspread",
    )
    platform.async_register_entity_service(
        "save",
        {vol.Required("filename"): cv.string},
        "save_gspread",
    )


class GspreadSensor(Entity):
    """Representation of a Google Sheet sensor."""

    def __init__(
        self, hass: HomeAssistantType, creds: ServiceAccountCredentials, sheet_name: str
    ):
        super().__init__()
        self._creds = creds
        self.hass = hass

        self._name = sheet_name
        self._state = None
        self._available = True
        self._sheet_id = None

        self._attrs: dict[str, Any] = {}

    async def log_to_gspread(self, date, period, amount):
        if period == "morning":
            period = "AM"
            cell = "B"
        else:
            period = "PM"
            cell = "C"

        client = gspread.authorize(self._creds)
        sheet = await self.hass.async_add_executor_job(client.open, self._name)
        sheet_instance = await self.hass.async_add_executor_job(sheet.get_worksheet, 0)

        records_data = await self.hass.async_add_executor_job(
            sheet_instance.get_all_records
        )

        insert_row = True

        i = 1
        for record in records_data:
            if insert_row:
                i = i + 1
                if record.get("Date") == date:
                    insert_row = False

        if insert_row:
            await self.hass.async_add_executor_job(sheet_instance.append_row, [date])
            i = i + 1

        await self.hass.async_add_executor_job(
            sheet_instance.update, cell + str(i), amount
        )

        self.schedule_update_ha_state(True)

    async def clear_gspread(self):
        client = gspread.authorize(self._creds)
        sheet = await self.hass.async_add_executor_job(client.open, self._name)
        sheet_instance = await self.hass.async_add_executor_job(sheet.get_worksheet, 0)

        await self.hass.async_add_executor_job(sheet_instance.clear)
        await self.hass.async_add_executor_job(
            sheet_instance.append_row, ["Date", "AM", "PM"]
        )

        self.schedule_update_ha_state(True)

    async def save_gspread(self, filename):
        """Save spreadsheet content as CSV file"""
        file = open(filename, "w")
        file.write("Date,AM,PM\n")
        content: list[dict] = self._attrs["content"]
        for rec in content:
            i = 0
            for e in rec:
                if i > 0:
                    file.write(",")
                i = i + 1
                file.write(str(rec[e]))
            file.write("\n")
        file.close()

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        if self._sheet_id is None:
            _LOGGER.warning("sheet_id is none, returning default id")
            return "1234567890"
        return self._sheet_id

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    @property
    def state(self) -> Optional[str]:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_update(self):
        try:
            client = gspread.authorize(self._creds)
            sheet = await self.hass.async_add_executor_job(client.open, self._name)

            if self._sheet_id is None:
                self._sheet_id = sheet.id

            sheet_instance = await self.hass.async_add_executor_job(
                sheet.get_worksheet, 0
            )
            all_records = await self.hass.async_add_executor_job(
                sheet_instance.get_all_records
            )
            self._attrs["content"] = all_records.copy()
            if len(all_records) > 0:
                last_record = all_records.pop()
                self._state = last_record["Date"]
            else:
                self._state = ""
            self._available = True
        except (ClientError, Exception):
            self._available = False
            _LOGGER.exception("Error retrieving data from Google Sheet.")
