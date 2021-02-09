"""
"ads-async info" is a command line utility for getting information about a
given TwinCAT3 PLC.
"""

import argparse
import json
import logging

from .. import service
from .utils import send_and_receive_service_udp

DESCRIPTION = __doc__

module_logger = logging.getLogger(__name__)


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument("host", type=str, help="PLC hostname or IP address")

    argparser.add_argument(
        "--table", action="store_true", help="Output information as a table"
    )

    return argparser


def get_plc_info(
    plc_hostname: str,
) -> dict:
    """
    Get a PLC's Net ID and other information from its IP address.

    Parameters
    ----------
    plc_hostname: str
        The PLC IP / hostname.

    Returns
    -------
    net_id : str
        The Net ID.
    """
    svc = service.SystemService()
    return send_and_receive_service_udp(
        svc,
        plc_hostname,
        svc.get_info(),
        command_id=service.SystemServiceRequestCommand.GET_INFO,
        logger=module_logger,
    )


def main(host, table=False):
    """
    Get information about a given TwinCAT3 PLC over UDP.

    Parameters
    ----------
    host : str
        Hostname or IP address of PLC.

    table : bool, optional
        Return results as a table instead of JSON.
    """
    response = get_plc_info(host)
    if table:
        # TODO
        ...

    result = json.dumps(response, indent=4)
    print(result)
    return response
