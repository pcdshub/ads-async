import ctypes
import logging
import pathlib

import pytest

import ads_async
from ads_async import constants, structs
from ads_async.protocol import Server, ServerCircuit, ServerConnection

logger = logging.getLogger(__name__)
MODULE_PATH = pathlib.Path(__file__).parent


@pytest.fixture(scope="module")
def tmc_filename() -> str:
    return str(MODULE_PATH / "kmono.tmc")


@pytest.fixture(scope="function")
def tmc_database(tmc_filename: str, caplog) -> ads_async.symbols.TmcDatabase:
    return ads_async.symbols.TmcDatabase(tmc_filename)


@pytest.fixture(scope="function")
def symbol(tmc_database) -> ads_async.symbols.Symbol:
    """Just one symbol from the TMC database."""
    for data_area in tmc_database.data_areas:
        for _, symbol in data_area.symbols.items():
            return symbol


@pytest.fixture(scope="function")
def server(tmc_database) -> Server:
    return Server(tmc_database, net_id="1.2.3.4.5.6")


@pytest.fixture(scope="function")
def server_connection(server) -> ServerConnection:
    return server.add_connection(
        our_address=("server_host", 0),
        their_address=("client_addr", 0),
    )


@pytest.fixture(scope="function")
def server_circuit(server_connection: ServerConnection) -> ServerCircuit:
    return server_connection.get_circuit("7.8.9.10.11.12")


def test_smoke_basic(server: Server):
    assert isinstance(server.ads_state, constants.AdsState)
    assert len(server.version) == 3
    assert server.name


def serialize_request(
    command: structs._AdsStructBase,
    invoke_id: int = 0,
    command_id: constants.AdsCommandId = None,
) -> bytes:
    if command is None:
        serialized = b""
        assert command_id is not None
    else:
        serialized = command.serialize()
        command_id = command.command_id

    aoe = structs.AoEHeader.create_request(
        source=structs.AmsAddr(structs.AmsNetId((1, 2, 3, 4, 5, 6)), 1),
        target=structs.AmsAddr(structs.AmsNetId((5, 4, 3, 2, 1, 7)), 2),
        command_id=command_id,
        length=len(serialized),
        invoke_id=invoke_id,
    )

    ams = structs.AmsTcpHeader(length=ctypes.sizeof(aoe) + len(serialized))
    logger.debug("Creating frame: %s %s %s", ams, aoe, command)

    parts = [ams, aoe, command or serialized]
    return parts, b"".join(bytes(p) for p in (ams, aoe, serialized))


def send_request(
    server_circuit: ServerCircuit,
    command: structs._AdsStructBase = None,
    command_id: constants.AdsCommandId = None,
    invoke_id: int = 0,
):
    commands, serialized = serialize_request(
        command, command_id=command_id, invoke_id=invoke_id
    )
    print("Sending")
    for idx, send in enumerate(commands, 1):
        print("\t", idx, send)
    print()

    for header, item in server_circuit.connection.received_data(serialized):
        response = server_circuit.handle_command(header=header, request=item)
        print("Received")
        print("\t", response)

    for item in response:
        item.serialize()  # smoke test

    return response


def test_read_state(server: Server, server_circuit: ServerCircuit):
    header, response = send_request(
        server_circuit, command_id=constants.AdsCommandId.READ_STATE
    )
    assert isinstance(response, structs.AdsReadStateResponse)
    assert response.ads_state == server.ads_state


def test_read_device_info(server: Server, server_circuit: ServerCircuit):
    header, response = send_request(
        server_circuit, command_id=constants.AdsCommandId.READ_DEVICE_INFO
    )
    assert isinstance(response, structs.AdsDeviceInfo)
    assert response.name == server.name
    assert response.version_tuple == server.version


def test_read_and_write_by_handle(
    server: Server,
    server_circuit: ServerCircuit,
    symbol: ads_async.symbols.Symbol,
):
    request = structs.AdsReadWriteRequest.create_handle_by_name_request(symbol.name)
    assert server.database.get_symbol_by_name(symbol.name) is symbol
    (response,) = send_request(server_circuit, command=request)
    assert isinstance(response, structs.AoEHandleResponse)
    handle = response.handle

    request = structs.AdsWriteRequest(
        constants.AdsIndexGroup.SYM_VALBYHND,
        handle,
        data=symbol.ctypes_data_type(1),
    )

    (response,) = send_request(server_circuit, command=request)
    assert isinstance(response, structs.AoEResponseHeader)
    assert response.result == 0

    request = structs.AdsReadRequest(
        constants.AdsIndexGroup.SYM_VALBYHND,
        handle,
        length=symbol.byte_size,
    )

    (response,) = send_request(server_circuit, command=request)
    assert isinstance(response, structs.AoEReadResponse)

    assert bytes(response.data) == bytes(symbol.ctypes_data_type(1))


def test_read_symbol_info_ex(
    server: Server, server_circuit: ServerCircuit, symbol: ads_async.symbols.Symbol
):
    request = structs.AdsReadWriteRequest.create_info_by_name_request(symbol.name)
    (response,) = send_request(server_circuit, command=request)
    entry = response.data  # TODO: only because this is in the same process...
    assert isinstance(entry, structs.AdsSymbolEntry)
    assert entry.name == symbol.name
    assert entry.data_type == symbol.data_type
