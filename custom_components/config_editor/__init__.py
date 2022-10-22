import logging
import os
from typing import Literal
import voluptuous as vol
from homeassistant.components import websocket_api
from atomicwrites import AtomicWriter
from homeassistant.scripts.check_config import check, async_check_config
import asyncio


DOMAIN = "config_editor"
_LOGGER = logging.getLogger(__name__)


class InvalidHAConfig(Exception):
    """Raised if the config is invalid."""

    def __init__(self, message="Invalid config..."):
        self.message = message
        super().__init__(self.message)


async def async_setup(hass, config):
    hass.components.websocket_api.async_register_command(websocket_create)
    hass.states.async_set(DOMAIN + ".version", 4)
    return True


@websocket_api.require_admin
@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): DOMAIN + "/ws",
        vol.Required("action"): str,
        vol.Required("file"): str,
        vol.Required("data"): str,
        vol.Required("ext"): str,
        vol.Optional("depth", default=2): int,
    }
)
async def websocket_create(hass, connection, msg):
    action = msg["action"]
    ext = msg["ext"]
    if ext not in ["yaml", "py", "json", "conf", "js", "txt", "log", "css", "all"]:
        ext = "yaml"

    def is_extension_ok(extension):
        if len(extension) < 2:
            return False
        return ext == "all" or extension.endswith("." + ext)

    yamlname = msg["file"].replace("../", "/").strip("/")

    if not is_extension_ok(msg["file"]):
        yamlname = "temptest." + ext

    _LOGGER.info("Loading " + yamlname)

    fullpath = hass.config.path(yamlname)
    if action == "load":
        _LOGGER.info("Loading " + fullpath)
        new_content = ""
        res = "Loaded"
        try:
            with open(fullpath, encoding="utf-8") as fdesc:
                new_content = fdesc.read()
        except Exception:
            res = "Reading Failed"
            _LOGGER.exception("Reading failed: %s", fullpath)
        finally:
            connection.send_result(
                msg["id"], {"msg": res + ": " + fullpath, "file": yamlname, "data": new_content, "ext": ext}
            )

    elif action == "save":
        _LOGGER.info("Saving " + fullpath + " via UI editor.")
        new_content = msg["data"]
        res = "Saved"
        try:
            dirnm = os.path.dirname(fullpath)
            mode = prepare_filesys(fullpath, dirnm)
            with open(fullpath, "r") as f_orig:
                old_content = f_orig.read()
            save_content_to_file(fullpath, new_content, mode)
            ret = await hass.async_add_executor_job(check, hass.config.path())
            if ret["except"]:
                _LOGGER.warning(ret["except"])
                err_msg = ",".join([f"{domain}: {config}" for domain, config in ret["except"].items()])
                raise InvalidHAConfig(err_msg)
        except InvalidHAConfig as err:
            res = "Saving failed with Invalid HA Config: " + err.message
            _LOGGER.exception(res + ": %s", fullpath)
            save_content_to_file(fullpath, old_content, mode)
        except Exception:
            res = "Saving Failed"
            _LOGGER.exception(res + ": %s", fullpath)
        finally:
            connection.send_result(msg["id"], {"msg": res + ": " + fullpath})

    elif action == "list":
        listyaml = ["/packages/customer_spec/static_config.yaml", "/ip_bans.yaml"]
        if len(listyaml) < 1:
            listyaml = ["list_error." + ext]
        _LOGGER.info(f"Loading files to edit: {listyaml}")
        connection.send_result(msg["id"], {"msg": str(len(listyaml)) + " File(s)", "file": listyaml, "ext": ext})


def prepare_filesys(fullpath, dirnm) -> Literal:
    """
    Create dir if not exists and check permissions.
    Return correct permissions.
    """
    if not os.path.isdir(dirnm):
        os.makedirs(dirnm, exist_ok=True)
    try:
        mode = os.stat(fullpath).st_mode
    except Exception:
        mode = 0o666
    return mode


def save_content_to_file(fullpath, content, mode):
    with AtomicWriter(fullpath, overwrite=True).open() as fdesc:
        fdesc.write(content)
    with open(fullpath, "a") as fdesc:
        try:
            os.fchmod(fdesc.fileno(), mode)
        except Exception:
            pass
