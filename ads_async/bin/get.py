"""
"ads-async get" is a command line utility to get symbol information from a
given TwinCAT3 PLC.
"""
import argparse
import json
import logging
from typing import Optional

from .. import constants
from .utils import setup_connection

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


async def get_symbols(
    plc_hostname: str,
    symbols: list[str],
    our_net_id: str,
    plc_net_id: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: str = "",
    include_exceptions: bool = True,
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
    result = {}
    async with setup_connection(
        plc_hostname,
        plc_net_id=plc_net_id,
        our_net_id=our_net_id,
        add_route=add_route,
        route_host=route_host,
        timeout=timeout,
    ) as (client, circuit):
        for symbol_name in symbols:
            symbol = circuit.get_symbol_by_name(symbol_name)
            try:
                result[symbol_name] = await symbol.read()
            except Exception as ex:
                if include_exceptions:
                    result[symbol_name] = f"(Exception) {ex.__class__.__name__} {ex}"

    return result


async def main(
    host: str,
    symbols: list[str],
    net_id: Optional[str] = None,
    our_net_id: Optional[str] = None,
    our_host: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: Optional[str] = None,
):
    result = await get_symbols(
        host,
        symbols,
        plc_net_id=net_id,
        our_net_id=our_net_id,
        add_route=add_route,
        route_host=our_host,
        timeout=timeout,
    )

    print(json.dumps(result, indent=4))
