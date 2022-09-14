import asyncio
import contextlib
import logging
import socket
from typing import Generator, Optional

from .. import constants, service
from ..asyncio.client import Client
from ..service import BadResponse

module_logger = logging.getLogger(__name__)


def get_netid_from_service_port(plc_hostname: str, timeout: float = 2.0) -> str:
    """
    Get a PLC Net ID from its service port.

    Parameters
    ----------
    plc_hostname : str
        The PLC hostname or IP address.
    timeout : float, optional
        The timeout period.

    Returns
    -------
    str
        Best attempt at the PLC Net ID.  Defaults to the PLC IP address with
        .1.1 at the end if the service port does not respond.
    """
    from .info import get_plc_info

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
    return plc_net_id


@contextlib.asynccontextmanager
async def setup_connection(
    plc_hostname: str,
    our_net_id: str,
    plc_net_id: Optional[str] = None,
    timeout: float = 2.0,
    add_route: bool = False,
    route_host: str = "",
):
    """
    Asynchronous context manager to configure a PLC connection + circuit.

    Parameters
    ----------
    plc_hostname : str
        The PLC hostname or IP address.
    our_net_id : str
        The client's AMS Net ID.
    plc_net_id : Optional[str], optional
        The PLC's AMS Net ID.  Defaults to (plc ip address).1.1 by convention.
    timeout : float, optional
        Timeout to use for service port requests to the PLC.
    add_route : bool, optional
        Add a route to the PLC prior to opening a circuit to the PLC.
    route_host : str, optional
        The route hostname to use, if adding a route.

    Returns
    -------
    client : Client
    circuit : AsyncioClientCircuit
    """
    if not our_net_id:
        if route_host:
            route_ip = socket.gethostbyname(route_host)
            our_net_id = f"{route_ip}.1.1"
    if not route_host and our_net_id:
        route_host = ".".join(our_net_id.split(".")[:4])

    if plc_net_id is None:
        loop = asyncio.get_running_loop()
        plc_net_id = await loop.run_in_executor(
            None, get_netid_from_service_port, plc_hostname, timeout
        )

    def add_route_now():
        from .route import add_route_to_plc

        add_route_to_plc(
            plc_hostname=plc_hostname,
            source_net_id=our_net_id,
            source_name=route_host,
            route_name=route_host,
        )

    if add_route:
        if not len(route_host):
            raise ValueError("Must specify a route host to add a route")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, add_route_now)

    async with Client(
        (plc_hostname, constants.ADS_TCP_SERVER_PORT), our_net_id=our_net_id
    ) as client:
        async with client.get_circuit(plc_net_id) as circuit:
            yield client, circuit


def _send_and_receive_udp(
    host: str,
    packet: bytes,
    port: int = constants.ADS_UDP_SERVER_PORT,
    recv_length: int = 1024,
    timeout: float = 1.0,
):
    """
    Send a UDP packet by way of stdlib socket, and yield responses.

    Parameters
    ----------
    host : str
        The hostname.

    packet : bytes
        The packet to send.

    port : int, optional
        The UDP port to send to.

    recv_length : int, optional
        The receive buffer size.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # Bind before send is necessary at least on win32
        sock.bind(("", 0))
        sock.settimeout(timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (host, port))

        while True:
            try:
                yield sock.recvfrom(recv_length)
            except socket.timeout:
                # Timeout, but give caller the option to retry
                yield TimeoutError()
                sock.sendto(packet, (host, port))
    finally:
        sock.close()


def send_and_receive_service_udp(
    svc: service.SystemService,
    plc_hostname: str,
    to_send: bytes,
    command_id: service.SystemServiceRequestCommand,
    timeout: float = 2.0,
    logger=None,
) -> Generator[dict, None, None]:
    """
    Send a system service UDP packet and await a response.

    Parameters
    ----------
    svc : SystemService
        SystemService protocol instance.

    plc_hostname: str
        The PLC IP / hostname, or broadcast address.

    to_send : bytes
        Packet to send.

    command_id : SystemServiceRequestCommand
        Command ID to check when verifying responses.

    timeout : float, optional
        Timeout after no responses for a certain period.

    logger : logging.Logger
        Logger instance for status information.

    Yields
    ------
    response : dict
        PLC response to request, in a user-friendly dict.
    """
    logger = logger or module_logger

    valid_responses = 0
    logger.debug("Sending %s to %s", to_send, plc_hostname)
    for recv in _send_and_receive_udp(plc_hostname, to_send, timeout=timeout):
        if isinstance(recv, TimeoutError):
            if valid_responses == 0:
                logger.debug("Timed out waiting for responses")
            break
        try:
            packet, addr = recv
            result = svc.deserialize_response(packet, addr)
            if result["command_id"] == command_id:
                valid_responses += 1
                yield result
        except BadResponse as ex:
            logger.warning("Got bad response; waiting: %s", ex)
            logger.debug("Got bad response; waiting: %s", ex, exc_info=ex)
