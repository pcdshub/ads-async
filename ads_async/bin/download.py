"""
"ads-async download" is a command line utility to download files from a given
TwinCAT3 PLC.
"""
import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
from typing import Any, Dict, List, Optional

from .. import constants
from ..asyncio.client import Client
from .info import get_plc_info
from .route import add_route_to_plc

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
    argparser.add_argument("filenames", type=str, nargs="+", help="File(s) to get")
    argparser.add_argument(
        "--save-to",
        type=str,
        default=".",
        help="Path to save files to.  Defaults to the working directory.",
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


@contextlib.asynccontextmanager
async def setup_connection(
    plc_hostname: str,
    our_net_id: str,
    plc_net_id: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: str = "",
):
    if not our_net_id:
        if route_host and route_host[0].isnumeric():
            our_net_id = f"{route_host}.1.1"
    if not route_host and our_net_id:
        route_host = ".".join(our_net_id.split(".")[:4])
    if plc_net_id is None:
        try:
            plc_info = next(get_plc_info(plc_hostname, timeout=timeout))
        except (TimeoutError, StopIteration):
            plc_ip = socket.gethostbyname(plc_hostname)
            plc_net_id = f"{plc_ip}.1.1"
            module_logger.warning(
                "Failed to get PLC information through service port; "
                "falling back to %s as Net ID.",
                plc_net_id,
            )
        else:
            plc_net_id = plc_info["source_net_id"]
            module_logger.info(
                "Got PLC net ID through service port: %s",
                plc_net_id,
            )

    if add_route:
        if not len(route_host):
            raise ValueError("Must specify a route host to add a route")
        add_route_to_plc(
            plc_hostname=plc_hostname,
            source_net_id=our_net_id,
            source_name=route_host,
            route_name=route_host,
        )

    async with Client(
        (plc_hostname, constants.ADS_TCP_SERVER_PORT), our_net_id=our_net_id
    ) as client:
        async with client.get_circuit(plc_net_id) as circuit:
            yield client, circuit


async def async_download(
    plc_hostname: str,
    filenames: List[str],
    our_net_id: str,
    save_to: str = ".",
    plc_net_id: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Download files from a PLC."""
    result = {}
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

            result[filename] = {
                "stat": stat,
                "data": data,
                "error": error,
            }

    return result


def main(
    host: str,
    filenames: List[str],
    save_to: str = ".",
    net_id: Optional[str] = None,
    our_net_id: Optional[str] = None,
    our_host: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: Optional[str] = None,
    stdout: bool = False,
):
    result = asyncio.run(
        async_download(
            host,
            filenames,
            save_to=save_to,
            plc_net_id=net_id,
            our_net_id=our_net_id,
            add_route=add_route,
            route_host=our_host,
            timeout=timeout,
        )
    )
    to_display = {}
    for filename, info in result.items():
        to_display[filename] = {}
        if stdout:
            if info["data"] is not None:
                sys.stdout.buffer.write(info["data"])
                sys.stdout.flush()
            else:
                module_logger.warning("Unable to download file: %s", info["error"])
        else:
            if info["stat"] is not None:
                to_display[filename]["stat"] = info["stat"].to_dict()

            if info["data"] is not None:
                bare_filename = os.path.split(filename)[-1]
                write_filename = os.path.join(save_to, bare_filename)
                with open(write_filename, "wb") as fp:
                    fp.write(info["data"])
                to_display[filename]["wrote_bytes"] = len(info["data"])
                to_display[filename]["wrote_to"] = write_filename
            else:
                err = info["error"]
                to_display[filename]["error"] = f"{err.__class__.__name__}: {err}"

    if not stdout:
        print(json.dumps(to_display, indent=4))