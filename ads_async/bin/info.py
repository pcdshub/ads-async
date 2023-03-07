"""
"ads-async info" is a command line utility for getting information about a
given TwinCAT3 PLC.
"""

import argparse
import json
import logging
from collections.abc import Generator

from .. import service
from .utils import send_and_receive_service_udp

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

    argparser.add_argument(
        "--table", action="store_true", help="Output information as a table"
    )
    argparser.add_argument(
        "--broadcast", action="store_true", help="Broadcast to multiple PLCs"
    )
    argparser.add_argument(
        "--timeout", type=float, default=2.0, help="Timeout for responses"
    )

    return argparser


def get_plc_info(
    plc_hostname: str,
    timeout: float = 2.0,
) -> Generator[dict, None, None]:
    """
    Get a PLC's Net ID and other information from its IP address.

    Parameters
    ----------
    plc_hostname: str
        PLC hostname, IP address, or broadcast address.

    timeout : float, optional
        Timeout for responses, in seconds.

    Yields
    ------
    info : dict
        PLC information dictionary.
    """
    svc = service.SystemService()
    yield from send_and_receive_service_udp(
        svc,
        plc_hostname,
        svc.get_info(),
        command_id=service.SystemServiceRequestCommand.GET_INFO,
        logger=module_logger,
        timeout=timeout,
    )


def main(host, table=False, broadcast=False, timeout=2.0):
    """
    Get information about a given TwinCAT3 PLC over UDP.

    Parameters
    ----------
    host : str
        PLC hostname, IP address, or broadcast address.

    timeout : float, optional
        Timeout for responses, in seconds.

    table : bool, optional
        Return results as a table instead of JSON.

    broadcast : bool, optional
        If in broadcast mode, multiple responses up to the timeout period will
        be waited for.
    """
    for response in get_plc_info(host, timeout=timeout):
        if table:
            # TODO
            ...

        result = json.dumps(response, indent=4)
        print(result)
        if not broadcast:
            break
