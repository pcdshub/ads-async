"""
"ads-async download" is a command line utility to download files from a given
TwinCAT3 PLC.
"""
import argparse
import dataclasses
import json
import logging
import os
import sys
from typing import AsyncGenerator, Optional

from .. import constants, structs
from .utils import setup_connection

DESCRIPTION = __doc__

module_logger = logging.getLogger(__name__)


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "host",
        type=str,
        help="PLC hostname or IP address",
    )
    argparser.add_argument(
        "filenames",
        type=str,
        nargs="*",
        help="File(s) to get",
    )
    argparser.add_argument(
        "--save-to",
        type=str,
        default=".",
        help="Path to save files to.  Defaults to the working directory.",
    )
    argparser.add_argument(
        "--project",
        action="store_true",
        help="Download the project as well",
    )
    argparser.add_argument(
        "--stdout",
        action="store_true",
        help="Write file contents to standard output, excluding the JSON summary",
    )
    argparser.add_argument(
        "--net-id",
        type=str,
        required=False,
        help="PLC Net ID (optional, can be determined with service requests)",
    )

    argparser.add_argument(
        "--add-route", action="store_true", help="Add a route if required"
    )
    argparser.add_argument(
        "--our-net-id",
        type=str,
        required=False,
        default=constants.ADS_ASYNC_LOCAL_NET_ID,
        help=(
            "Net ID to report as the client (environment variable "
            "ADS_ASYNC_LOCAL_NET_ID)"
        ),
    )
    argparser.add_argument(
        "--our-host",
        type=str,
        default=constants.ADS_ASYNC_LOCAL_IP,
        help=(
            "Host or IP to report when adding a route (environment variable "
            "ADS_ASYNC_LOCAL_IP)"
        ),
    )
    argparser.add_argument(
        "--timeout", type=float, default=2.0, help="Timeout for responses"
    )
    return argparser


@dataclasses.dataclass
class DownloadItem:
    #: The remote filename.  None if referring to a failed project download.
    filename: Optional[str] = None
    #: File status information.
    stat: Optional[structs.AdsFileStat] = None
    #: Data contained in the file, if successfully downloaded.
    data: Optional[bytes] = None
    #: Exception raised during the download process.  Only set if ``data`` is
    #: None.
    error: Optional[Exception] = None


async def async_download(
    plc_hostname: str,
    filenames: list[str],
    our_net_id: str,
    plc_net_id: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    project: bool = False,
    route_host: str = "",
) -> AsyncGenerator[DownloadItem, None]:
    """Download files and/or projects from a PLC."""
    async with setup_connection(
        plc_hostname,
        plc_net_id=plc_net_id,
        our_net_id=our_net_id,
        add_route=add_route,
        route_host=route_host,
        timeout=timeout,
    ) as (client, circuit):
        for filename in filenames:
            try:
                stat = await circuit.get_file_stat(filename)
            except Exception:
                stat = None

            try:
                data = await circuit.get_file(filename)
            except Exception as ex:
                data = None
                error = ex
            else:
                error = None

            yield DownloadItem(
                filename=filename,
                stat=stat,
                data=data,
                error=error,
            )

        if project:
            try:
                projects = await circuit.download_projects()
            except Exception as ex:
                yield DownloadItem(
                    filename=None,
                    stat=None,
                    data=None,
                    error=ex,
                )
            else:
                for fn, zipfile in projects.items():
                    yield DownloadItem(
                        filename=fn,
                        data=zipfile.get_raw_data(),
                        stat=None,
                        error=None,
                    )


async def main(
    host: str,
    filenames: list[str],
    save_to: str = ".",
    net_id: Optional[str] = None,
    our_net_id: Optional[str] = None,
    our_host: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: Optional[str] = None,
    project: bool = False,
    stdout: bool = False,
):
    to_display = {}
    async for info in async_download(
        host,
        filenames,
        plc_net_id=net_id,
        our_net_id=our_net_id,
        add_route=add_route,
        route_host=our_host,
        timeout=timeout,
        project=project,
    ):
        filename = info.filename or "(project)"
        to_display[filename] = {}
        if stdout:
            if info.data is not None:
                sys.stdout.buffer.write(info.data)
                sys.stdout.flush()
            else:
                module_logger.warning("Unable to download file: %s", info.error)
        else:
            if info.stat is not None:
                to_display[filename]["stat"] = info.stat.to_dict()

            if info.data is not None:
                bare_filename = os.path.split(filename)[-1]
                write_filename = os.path.join(save_to, bare_filename)
                with open(write_filename, "wb") as fp:
                    fp.write(info.data)
                to_display[filename]["wrote_bytes"] = len(info.data)
                to_display[filename]["wrote_to"] = write_filename
            else:
                to_display[filename][
                    "error"
                ] = f"{info.error.__class__.__name__}: {info.error}"

    if not stdout:
        print(json.dumps(to_display, indent=4))
