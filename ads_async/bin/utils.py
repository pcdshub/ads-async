import logging
import socket

from .. import constants, service
from ..service import BadResponse

module_logger = logging.getLogger(__name__)


def _send_and_receive_udp(
    host: str,
    packet: bytes,
    port: int = constants.ADS_UDP_SERVER_PORT,
    recv_length: int = 1024,
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
        sock.sendto(packet, (host, port))
        sock.settimeout(1)

        while True:
            try:
                yield sock.recvfrom(recv_length)
            except socket.timeout:
                # Timeout, but give caller the option to retry
                raise TimeoutError()
                sock.sendto(packet, (host, port))
    finally:
        sock.close()


def send_and_receive_service_udp(
    svc: service.SystemService,
    plc_hostname: str,
    to_send: bytes,
    command_id: service.SystemServiceRequestCommand,
    logger=None,
) -> dict:
    """
    Get a PLC's Net ID from its IP address.

    Parameters
    ----------
    plc_hostname: str
        The PLC IP / hostname.

    Returns
    -------
    net_id : str
        The Net ID.
    """
    logger = logger or module_logger

    # TODO max timeout/retries

    logger.debug("Sending %s to %s", to_send, plc_hostname)
    for recv in _send_and_receive_udp(plc_hostname, to_send):
        if isinstance(recv, TimeoutError):
            logger.warning("Timeout; waiting")
            continue
        try:
            packet, addr = recv
            result = svc.deserialize_response(packet, addr)
            if result["command_id"] != command_id:
                raise BadResponse("Not matching response")
            return result
        except BadResponse as ex:
            logger.warning("Got bad response; waiting: %s", ex)
            logger.debug("Got bad response; waiting: %s", ex, exc_info=ex)
