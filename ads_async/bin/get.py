"""
"ads-async get" is a command line utility to get symbol information from a
given TwinCAT3 PLC.
"""
import argparse
import asyncio
import json
import logging
from typing import List, Optional

from .. import constants
from ..asyncio.client import Client
from . import utils
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
        "host", type=str, help="PLC hostname, IP address, or broadcast address"
    )
    argparser.add_argument("symbols", type=str, nargs="+", help="Symbols to get")
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
        required=not len(utils.ADS_ASYNC_CLIENT_NET_ID or ""),
        default=utils.ADS_ASYNC_CLIENT_NET_ID,
        help=(
            "Net ID to report as the client (environment variable "
            "ADS_ASYNC_CLIENT_NET_ID)"
        ),
    )
    argparser.add_argument(
        "--our-host",
        type=str,
        default=utils.ADS_ASYNC_CLIENT_IP,
        help=(
            "Host or IP to report when adding a route (environment variable "
            "ADS_ASYNC_CLIENT_IP)"
        ),
    )
    argparser.add_argument(
        "--timeout", type=float, default=2.0, help="Timeout for responses"
    )
    return argparser


def get_symbols(
    plc_hostname: str,
    symbols: List[str],
    our_net_id: str,
    plc_net_id: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: str = "",
) -> dict:
    """
    Get symbol values from a PLC.

    Parameters
    ----------
    plc_hostname: str
        PLC hostname, IP address, or broadcast address.

    plc_net_id : str, optional
        PLC Net ID.

    Yields
    ------
    symbol_name : str
        Symbol name.
    symbol_value : any
        Symbol value.
    """
    if plc_net_id is None:
        try:
            plc_info = next(get_plc_info(plc_hostname, timeout=timeout))
        except TimeoutError:
            plc_net_id = f"{plc_hostname}.1.1"
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

    async def main():
        result = {}
        async with Client(
            (plc_hostname, constants.ADS_TCP_SERVER_PORT), our_net_id=our_net_id
        ) as client:
            async with client.get_circuit(plc_net_id) as circuit:
                for symbol_name in symbols:
                    symbol = circuit.get_symbol_by_name(symbol_name)
                    result[symbol_name] = await symbol.read()
        return result

    return asyncio.run(main())


def main(
    host,
    symbols,
    net_id=None,
    our_net_id=None,
    our_host=None,
    timeout=2.0,
    add_route=False,
    route_host=None,
):
    result = get_symbols(
        host,
        symbols,
        plc_net_id=net_id,
        our_net_id=our_net_id,
        add_route=add_route,
        route_host=our_host,
        timeout=timeout,
    )

    print(json.dumps(result, indent=4))
