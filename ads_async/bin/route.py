"""
"ads-async route" is a command line utility for adding a route to a given
TwinCAT3 PLC.
"""

import argparse
import json
import logging
from typing import Optional

from .. import service
from . import utils
from .utils import send_and_receive_service_udp

DESCRIPTION = __doc__

module_logger = logging.getLogger(__name__)


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument("host", type=str, help="PLC hostname or IP address")
    argparser.add_argument("source_net_id", type=str, help="Our reported Net ID")
    argparser.add_argument(
        "source_name",
        type=str,
        help="Our reported name, used for the route name by default",
    )
    argparser.add_argument(
        "--username", default=utils.ADS_ASYNC_USERNAME, type=str, help="Login username"
    )
    argparser.add_argument(
        "--password", default=utils.ADS_ASYNC_PASSWORD, type=str, help="Login password"
    )
    argparser.add_argument("--route-name", default=None, type=str, help="Route name")
    argparser.add_argument(
        "--add-net-id",
        default=None,
        type=str,
        help="Net ID to add, if different than the source",
    )

    argparser.add_argument(
        "--table", action="store_true", help="Output information as a table"
    )

    return argparser


def add_route_to_plc(
    plc_hostname: str,
    source_net_id: str,
    source_name: str,
    username: str = utils.ADS_ASYNC_USERNAME,
    password: str = utils.ADS_ASYNC_PASSWORD,
    route_name: Optional[str] = None,
    net_id_to_add: Optional[str] = None,
) -> dict:
    """
    Add a new static route to a PLC.

    Parameters
    ----------
    plc_hostname: str
        The PLC IP / hostname.
    source_net_id : str
        Our reported Net ID.
    source_name : str
        Host name (or IP) of the host for the route to add.
    username : str, optional
        Username for PLC (TwinCAT3 default 'Administrator').
    password : str, optional
        Password for PLC (TwinCAT3 default '1').
    route_name : str, optional
        PLC side name for route, defaults to source_name.
    net_id_to_add : str, optional
        net id that is being added to the PLC, defaults to source_net_id.

    Returns
    -------
    info : dict
        Information dictionary of the response.
    """
    svc = service.SystemService()
    for result in send_and_receive_service_udp(
        svc,
        plc_hostname,
        svc.add_route_to_plc(
            source_net_id=source_net_id,
            source_name=source_name,
            username=username,
            password=password,
            route_name=route_name,
            net_id_to_add=net_id_to_add,
        ),
        command_id=service.SystemServiceRequestCommand.ADD_ROUTE,
        logger=module_logger,
    ):
        # TODO: I'm not sure we really want to add broadcast support here.
        return result


def main(
    host: str,
    source_net_id: str,
    source_name: str,
    *,
    username: str = utils.ADS_ASYNC_USERNAME,
    password: str = utils.ADS_ASYNC_PASSWORD,
    route_name: Optional[str] = None,
    add_net_id: Optional[str] = None,
    table: bool = False,
):
    """
    Get information about a given TwinCAT3 PLC over UDP.

    Parameters
    ----------
    host : str
        Hostname or IP address of PLC.

    table : bool, optional
        Return results as a table instead of JSON.
    """
    response = add_route_to_plc(
        host,
        source_net_id=source_net_id,
        source_name=source_name,
        username=username,
        password=password,
        route_name=route_name,
        net_id_to_add=add_net_id,
    )
    if table:
        # TODO
        ...

    result = json.dumps(response, indent=4)
    print(result)
    return response
