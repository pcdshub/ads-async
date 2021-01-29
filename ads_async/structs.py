import ctypes
import enum
import functools
import ipaddress
import struct
import typing

from . import constants
from .constants import AoEHeaderFlag

T_AdsStructure = typing.TypeVar('T_AdsStructure', bound='_AdsStructBase')
T_Serializable = typing.Union[T_AdsStructure, bytes, ctypes._SimpleCData]
T_PayloadField = typing.Tuple[str, str, int, typing.Callable, typing.Callable]
_T_Commands = typing.Dict[constants.AdsCommandId,
                          typing.Dict[str, T_AdsStructure]]

_commands = {}  # type: _T_Commands


def _use_for(key: str, command_id: constants.AdsCommandId,
             cls: T_AdsStructure) -> T_AdsStructure:
    """A decorator that populates the `_commands` dictionary by command ID."""
    if command_id not in _commands:
        _commands[command_id] = {}

    _commands[command_id][key] = cls
    cls._command_id = command_id
    return cls


def use_for_response(command_id: constants.AdsCommandId):
    """
    Decorator marking a class to be used for a specific AdsCommand response.
    """
    return functools.partial(_use_for, 'response', command_id)


def use_for_request(command_id: constants.AdsCommandId):
    """
    Decorator marking a class to be used for a specific AdsCommand request.
    """
    return functools.partial(_use_for, 'request', command_id)


def get_struct_by_command(command_id: constants.AdsCommandId,
                          request: bool) -> T_AdsStructure:
    """
    Get a structure class given the AdsCommandId.

    Parameters
    ----------
    command_id : AdsCommandId
        The command ID.

    request : bool
        Request (True) or response (False).

    Returns
    -------
    struct : _AdsStructBase
        The structure class.
    """

    key = 'request' if request else 'response'
    return _commands[command_id][key]


def string_to_byte_string(s: str) -> bytes:
    """Encode a string using the configured string encoding."""
    return bytes(s, constants.ADS_ASYNC_STRING_ENCODING)


def byte_string_to_string(s: bytes) -> str:
    """Decode a string using the configured string encoding."""
    value = str(s, constants.ADS_ASYNC_STRING_ENCODING)
    return value.split('\x00')[0]


def serialize(obj: T_Serializable) -> bytes:
    """
    Serialize a given item.

    Returns
    -------
    serialized : bytes
    """
    # TODO: better way around this? would like to bake it into __bytes__
    if hasattr(obj, 'serialize'):
        return obj.serialize()  # type: ignore
    return bytes(obj)  # type: ignore


class _AdsStructBase(ctypes.LittleEndianStructure):
    """
    A base structure for the rest in :mod:`ads_async.structs`.

    Handles:
        - Special reprs by way of `to_dict` (and `_dict_mapping`).
        - Payload serialization / deserialization by way of `_payload_fields`
          and `from_buffer_extended`.
        - Removing alignment by way of _pack_
    """
    # To be overridden by subclasses:
    _command_id: constants.AdsCommandId = constants.AdsCommandId.INVALID

    _pack_ = 1
    _dict_mapping: typing.Dict[str, str] = {}
    _payload_fields: typing.List[T_PayloadField] = []

    def __init_subclass__(cls):
        super().__init_subclass__()

        dict_mapping = {}
        all_fields = []
        for base in reversed(cls.mro()):
            all_fields.extend(getattr(base, '_fields_', []))
            dict_mapping.update(getattr(base, '_dict_mapping', {}))

        cls._all_fields_ = all_fields
        cls._dict_mapping = dict_mapping
        cls._dict_attrs = [
            cls._dict_mapping.get(attr, attr)
            for attr, *info in cls._all_fields_
        ]
        cls._dict_attrs.extend([item[0] for item in cls._payload_fields])
        cls._dict_attrs = [attr for attr in cls._dict_attrs
                           if not attr.startswith('_')]

    def to_dict(self) -> dict:
        """Return the structure as a dictionary."""
        # Raw values can be retargeted to properties by way of _dict_mapping:
        #  {'raw_attr': 'property_attr'}
        return {attr: getattr(self, attr)
                for attr in self._dict_attrs}

    def __repr__(self):
        formatted_args = ", ".join(f"{k!s}={v!r}"
                                   for k, v in self.to_dict().items())
        return f"{self.__class__.__name__}({formatted_args})"

    def serialize(self) -> bytearray:
        """Serialize the structure and payload."""
        packed = bytearray(self)

        for attr, fmt, padding, _, serialize in self._payload_fields:
            fmt = fmt.format(self=self)
            # length = struct.calcsize(fmt)
            value = getattr(self, attr)
            if serialize is not None:
                value = serialize(value)
            packed.extend(struct.pack(fmt, value) + b'\00' * padding)

        return packed

    @classmethod
    def from_buffer_extended(cls: typing.Type[T_AdsStructure],
                             buf: typing.Union[memoryview, bytearray]
                             ) -> T_AdsStructure:
        """
        Deserialize data from `buf` into a structure + payload.

        Parameters
        ----------
        buf : bytearray
            The byte buffer.

        Returns
        -------
        T_AdsStructure
        """
        # TODO: this is a workaround to not being able to override
        # `from_buffer`
        if not cls._payload_fields:
            return cls.from_buffer(buf)

        new_struct = cls.from_buffer(buf)

        payload_buf = memoryview(buf)[ctypes.sizeof(cls):]
        for attr, fmt, padding, deserialize, serialize in cls._payload_fields:
            fmt = fmt.format(self=new_struct)
            length = struct.calcsize(fmt)
            value = struct.unpack(fmt, payload_buf[:length])
            payload_buf = payload_buf[length + padding:]
            if deserialize is not None:
                value = deserialize(*value)
            setattr(new_struct, attr, value)

        return new_struct

    @property
    def command_id(self) -> constants.AdsCommandId:
        """The command ID associated with this request/response."""
        return self._command_id


def _enum_property(field_name: str, enum_cls: typing.Type[enum.Enum],
                   *, doc: str = None, strict: bool = True
                   ) -> property:
    """
    Create a property which makes a field value into an enum value.

    Parameters
    ----------
    field_name : str
        The field name (i.e., parameter 1 in the list of _fields_)

    enum_cls : enum.Enum
        The enum class.

    doc : str, optional
        Documentation for the property.

    strict : bool, optional
        Require values on get (and set) to be valid enum values.  If False, get
        may return raw values, and set may accept raw values (unknown or
        unacceptable enum values).
    """

    def fget(self):
        value = getattr(self, field_name)
        try:
            return enum_cls(value)
        except ValueError:
            if not strict:
                return value
            raise

    def fset(self, value):
        if strict:
            # Raises ValueError if invalid
            value = enum_cls(value).value

        setattr(self, field_name, value)

    return property(fget, fset, doc=doc)


def _create_byte_string_property(field_name: str, *, doc: str = None,
                                 encoding=constants.ADS_ASYNC_STRING_ENCODING):
    """
    Create a property which makes handling byte string fields more convenient.

    Parameters
    ----------
    field_name : str
        The field name (i.e., parameter 1 in the list of _fields_)

    doc : str, optional
        Documentation for the property.

    encoding : str, optional
        Attempt to decode with this decoding on access (or encode when
        writing).
    """

    def fget(self) -> str:
        value = getattr(self, field_name)
        try:
            return value.decode(encoding)
        except ValueError:
            return value

    def fset(self, value: typing.Union[str, bytes]):
        if isinstance(value, str):
            value = value.encode(encoding)
        setattr(self, field_name, value)

    return property(fget, fset, doc=doc)


class AmsNetId(_AdsStructBase):
    """
    The NetId of and ADS device can be represented in this structure.

    Net IDs are do not necessarily have to have a relation to an IP address,
    though by convention it may be wise to configure them similarly.
    """
    octets: ctypes.c_uint8

    _fields_ = [
        ('octets', ctypes.c_uint8 * 6),
    ]

    def __repr__(self):
        return '.'.join(str(c) for c in self.octets)

    @classmethod
    def from_ipv4(cls: typing.Type[T_AdsStructure],
                  ip: typing.Union[str, ipaddress.IPv4Address],
                  octet5: int = 1,
                  octet6: int = 1) -> T_AdsStructure:
        """
        Create an AMS Net ID based on an IPv4 address.

        Parameters
        ----------
        ip : ipaddress.IPv4Address or str
            The IP address to base the Net ID on.

        octet5 : int
            The 5th octet (i.e., 5 of 1.2.3.4.5.6).

        octet5 : int
            The 6th octet (i.e., 6 of 1.2.3.4.5.6).

        Returns
        -------
        net_id : AmsNetId
        """
        if not isinstance(ip, ipaddress.IPv4Address):
            ip = ipaddress.IPv4Address(ip)

        return cls(tuple(ip.packed) + (octet5, octet6))

    @classmethod
    def from_string(cls: typing.Type[T_AdsStructure],
                    addr: str) -> T_AdsStructure:
        """
        Create an AMS Net ID based on an AMS ID string.

        Parameters
        ----------
        addr : str
            The net ID string.

        Returns
        -------
        net_id : AmsNetId
        """
        try:
            parts = tuple(int(octet) for octet in addr.split('.'))
            if len(parts) != 6:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(f'Not a valid AMS Net ID: {addr}')

        return cls(parts)


class AmsAddr(_AdsStructBase):
    """The full address of an ADS device can be stored in this structure."""
    net_id: AmsNetId
    _port: int

    _fields_ = [
        ('net_id', AmsNetId),

        # AMS Port number
        ('_port', ctypes.c_uint16),
    ]

    port = _enum_property('_port', constants.AmsPort, strict=False)
    _dict_mapping = {'_port': 'port'}

    def __repr__(self):
        port = self.port
        if hasattr(port, 'value'):
            return f'{self.net_id}:{self.port.value}({self.port.name})'
        return f'{self.net_id}:{self.port}'


class AdsVersion(_AdsStructBase):
    """Contains the version number, revision number and build number."""

    version: int
    revision: int
    build: int

    _fields_ = [
        ('version', ctypes.c_uint8),
        ('revision', ctypes.c_uint8),
        ('build', ctypes.c_uint16),
    ]


@use_for_response(constants.AdsCommandId.READ_DEVICE_INFO)
class AdsDeviceInfo(AdsVersion):
    """Contains the version number, revision number and build number."""
    _name: ctypes.c_char

    _fields_ = [
        # Inherits version information from AdsVersion
        ('_name', ctypes.c_char * 16),  # type: ignore
    ]

    name = _create_byte_string_property(
        '_name', encoding=constants.ADS_ASYNC_STRING_ENCODING)
    _dict_mapping = {'_name': 'name'}

    def __init__(self, version: int, revision: int, build: int, name: str):
        super().__init__()
        self.version = version
        self.revision = revision
        self.build = build
        self.name = name

    @property
    def version_tuple(self) -> typing.Tuple[int, int, int]:
        """The version tuple: (Version, Revision, Build)"""
        return (self.version, self.revision, self.build)


class AdsNotificationAttrib(_AdsStructBase):
    """
    Contains all the attributes for the definition of a notification.

    The ADS DLL is buffered from the real time transmission by a FIFO.
    TwinCAT first writes every value that is to be transmitted by means
    of the callback function into the FIFO. If the buffer is full, or if
    the nMaxDelay time has elapsed, then the callback function is invoked
    for each entry. The nTransMode parameter affects this process as follows:

    ADSTRANS_SERVERCYCLE
    The value is written cyclically into the FIFO at intervals of
    nCycleTime. The smallest possible value for nCycleTime is the cycle
    time of the ADS server; for the PLC, this is the task cycle time.
    The cycle time can be handled in 1ms steps. If you enter a cycle time
    of 0 ms, then the value is written into the FIFO with every task cycle.

    ADSTRANS_SERVERONCHA
    A value is only written into the FIFO if it has changed. The real-time
    sampling is executed in the time given in nCycleTime. The cycle time
    can be handled in 1ms steps. If you enter 0 ms as the cycle time, the
    variable is written into the FIFO every time it changes.

    Warning: Too many read operations can load the system so heavily that
    the user interface becomes much slower.

    Tip: Set the cycle time to the most appropriate values, and always
    close connections when they are no longer required.
    """

    callback_length: int
    _transmission_mode: int
    max_delay: int
    cycle_time: int

    _fields_ = [
        # Length of the data that is to be passed to the callback function.
        ('callback_length', ctypes.c_uint32),

        #  AdsTransmissionMode.SERVERCYCLE: The notification's callback
        #  function is invoked cyclically.
        #  AdsTransmissionMode.SERVERONCHA: The notification's callback
        #  function is only invoked when the value changes.
        ('_transmission_mode', ctypes.c_uint32),

        # The notification's callback function is invoked at the latest when
        # this time has elapsed. The unit is 100 ns.
        ('max_delay', ctypes.c_uint32),

        # The ADS server checks whether the variable has changed after this
        # time interval. The unit is 100 ns.  This can be repurposed as
        # "change_filter" in certain scenarios.
        ('cycle_time', ctypes.c_uint32),
    ]

    transmission_mode = _enum_property(
        '_transmission_mode', constants.AdsTransmissionMode,
        doc='Transmission mode settings (see AdsTransmissionMode)',
    )

    _dict_mapping = {'_transmission_mode': 'transmission_mode'}


class AdsNotificationLogMessage(_AdsStructBase):
    _fields_ = [
        ('timestamp', ctypes.c_uint64),
        ('unknown', ctypes.c_uint32),
        ('ams_port', ctypes.c_uint32),
        ('_sender_name', ctypes.c_ubyte * 16),
        ('message_length', ctypes.c_uint32),
        # message
    ]

    _payload_fields = [
        ('message', '{self.message_length}s', 0, bytes, serialize),
    ]

    _dict_mapping = {'_sender_name': 'sender_name'}

    @property
    def sender_name(self):
        return bytes(self._sender_name).split(b'\0')[0]


class AdsNotificationHeader(_AdsStructBase):
    """This structure is also passed to the callback function."""

    notification_handle: int
    timestamp: int
    sample_size: int

    _fields_ = [
        # Handle for the notification. Is specified when the notification is
        # defined.
        ('notification_handle', ctypes.c_uint32),

        # Contains a 64-bit value representing the number of 100-nanosecond
        # intervals since January 1, 1601 (UTC).
        ('timestamp', ctypes.c_uint64),

        # Number of bytes transferred.
        ('sample_size', ctypes.c_uint32),
    ]


# *
#  @brief Type definition of the callback function required by the
#  AdsSyncAddDeviceNotificationReqEx() function.
#   pAddr Structure with NetId and port number of the ADS server.
#   pNotification pointer to a AdsNotificationHeader structure
#   hUser custom handle pass to AdsSyncAddDeviceNotificationReqEx()
#  during registration
# /
# typedef void (* PAdsNotificationFuncEx)(const AmsAddr* pAddr, const
# AdsNotificationHeader* pNotification, ctypes.c_uint32 hUser);


class AdsNotificationSample(_AdsStructBase):
    _fields_ = [
        # Handle for the notification. Is specified when the notification is
        # defined.
        ('notification_handle', ctypes.c_uint32),

        # Number of bytes transferred.
        ('sample_size', ctypes.c_uint32),
        ('_data_start', ctypes.c_ubyte * 0),
    ]

    _payload_fields = [
        ('data', '{self.sample_size}s', 0, bytes, serialize),
    ]
    _dict_mapping = {'_data_start': 'data'}

    def as_log_message(self) -> AdsNotificationLogMessage:
        return AdsNotificationLogMessage.from_buffer_extended(bytearray(self.data))


class AdsNotificationStampHeader(_AdsStructBase):
    _fields_ = [
        # Contains a 64-bit value representing the number of 100-nanosecond
        # intervals since January 1, 1601 (UTC).
        ('timestamp', ctypes.c_uint64),

        # Number of bytes transferred.
        ('num_samples', ctypes.c_uint32),
        ('_sample_start', ctypes.c_ubyte * 0),
    ]

    _dict_mapping = {'_sample_start': 'samples'}

    @classmethod
    def from_buffer_extended(cls: typing.Type[T_AdsStructure],
                             buf: typing.Union[memoryview, bytearray]
                             ) -> T_AdsStructure:

        new_struct = cls.from_buffer(buf)
        payload_buf = memoryview(buf)[ctypes.sizeof(cls):]

        new_struct.byte_size = 0
        new_struct.samples = []
        for idx in range(new_struct.num_samples):
            sample = AdsNotificationSample.from_buffer_extended(payload_buf)
            print('\n' * 3, sample.as_log_message())
            new_struct.samples.append(sample)
            consumed = (
                ctypes.sizeof(AdsNotificationSample) + sample.sample_size
            )
            new_struct.byte_size += consumed
            payload_buf = payload_buf[consumed:]

        return new_struct

    # def __repr__(self):
    #     return (
    #         f"<{self.__class__.__name__} timestamp={self.timestamp} "
    #         f"num_samples={self.num_samples} samples={self.samples}>"
    #     )


@use_for_request(constants.AdsCommandId.DEVICE_NOTIFICATION)
@use_for_response(constants.AdsCommandId.DEVICE_NOTIFICATION)
class AdsNotificationStream(_AdsStructBase):
    """This structure is also passed to the callback function."""

    _fields_ = [
        ('length', ctypes.c_uint32),
        ('num_stamps', ctypes.c_uint32),
        ('_stamp_start', ctypes.c_ubyte * 0),
    ]

    _dict_mapping = {'_stamp_start': 'stamps'}

    @classmethod
    def from_buffer_extended(cls: typing.Type[T_AdsStructure],
                             buf: typing.Union[memoryview, bytearray]
                             ) -> T_AdsStructure:

        new_struct = cls.from_buffer(buf)
        payload_buf = memoryview(buf)[ctypes.sizeof(cls):]

        new_struct.stamps = []
        for stamp in range(new_struct.num_stamps):
            stamp = AdsNotificationStampHeader.from_buffer_extended(payload_buf)
            new_struct.stamps.append(stamp)
            payload_buf = payload_buf[stamp.byte_size:]

        return new_struct

    # def __repr__(self):
    #     return (
    #         f"<{self.__class__.__name__} length={self.length} "
    #         f"num_stamps={self.num_stamps} stamps={self.stamps}>"
    #     )


class AdsSymbolEntry(_AdsStructBase):
    """
    This structure describes the header of ADS symbol information

    Calling AdsSyncReadWriteReqEx2 with IndexGroup == ADSIGRP_SYM_INFOBYNAMEEX
    will return ADS symbol information in the provided readData buffer.
    The header of that information is structured as AdsSymbolEntry and can
    be followed by zero terminated strings for "symbol name", "type name"
    and a "comment"
    """
    entry_length: int
    index_group: constants.AdsIndexGroup
    index_offset: int
    size: int
    _data_type: int
    _flags: int
    name_length: int
    type_length: int
    comment_length: int
    _name: str = ''
    _type_name: str = ''
    _comment: str = ''

    _fields_ = [
        # length of complete symbol entry
        ('entry_length', ctypes.c_uint32),
        # indexGroup of symbol: input, output etc.
        ('_index_group', ctypes.c_uint32),
        # indexOffset of symbol
        ('index_offset', ctypes.c_uint32),
        # size of symbol ( in bytes, 0 = bit )
        ('size', ctypes.c_uint32),
        # adsDataType of symbol
        ('_data_type', ctypes.c_uint32),
        # see ADSSYMBOLFLAG_*
        ('_flags', ctypes.c_uint32),
        # length of symbol name (null terminating character not counted)
        ('name_length', ctypes.c_uint16),
        # length of type name (null terminating character not counted)
        ('type_length', ctypes.c_uint16),
        # length of comment (null terminating character not counted)
        ('comment_length', ctypes.c_uint16),
        ('_data_start', ctypes.c_uint8 * 0),
    ]

    _payload_fields = [
        ('_name', '{self.name_length}s', 1,
         byte_string_to_string, string_to_byte_string),
        ('_type_name', '{self.type_length}s', 1,
         byte_string_to_string, string_to_byte_string),
        ('_comment', '{self.comment_length}s', 1,
         byte_string_to_string, string_to_byte_string),
    ]

    flags = _enum_property('_flags', constants.AdsSymbolFlag)
    data_type = _enum_property('_data_type', constants.AdsDataType)
    index_group = _enum_property('_index_group',  # type: ignore
                                 constants.AdsIndexGroup,
                                 strict=False)
    _dict_mapping = {'_flags': 'flags',
                     '_data_type': 'data_type',
                     '_index_group': 'index_group',
                     }

    def __init__(self,
                 index_group: constants.AdsIndexGroup,
                 index_offset: int,
                 size: int,
                 data_type: constants.AdsDataType,
                 flags: constants.AdsSymbolFlag,
                 name: str,
                 type_name: str,
                 comment: str):
        super().__init__(0, index_group, index_offset, size, data_type, flags,
                         0, 0, 0,)
        self._name = name
        self._type_name = type_name
        self._comment = comment
        self._update_lengths()

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value):
        self._name = value
        self._update_lengths()

    @property
    def type_name(self) -> str:
        return self._type_name

    @type_name.setter
    def type_name(self, value):
        self._type_name = value
        self._update_lengths()

    @property
    def comment(self) -> str:
        return self._comment

    @comment.setter
    def comment(self, value):
        self._comment = value
        self._update_lengths()

    def _update_lengths(self):
        self.name_length = len(self.name)
        self.type_length = len(self.type_name)
        self.comment_length = len(self.comment)
        self.entry_length = sum((
            ctypes.sizeof(self),
            self.name_length, 1,  # plus null terminator
            self.type_length, 1,  # plus null terminator
            self.comment_length, 1,  # plus null terminator
        ))

    def serialize(self) -> bytearray:
        self._update_lengths()
        return super().serialize()


@use_for_request(constants.AdsCommandId.WRITE)
class AdsWriteRequest(_AdsStructBase):
    """
    """
    index_group: constants.AdsIndexGroup
    index_offset: int
    write_length: int
    _data_start: ctypes.c_ubyte
    data: typing.Any = None

    _fields_ = [
        ('_index_group', ctypes.c_uint32),
        ('index_offset', ctypes.c_uint32),
        ('write_length', ctypes.c_uint32),
        ('_data_start', ctypes.c_ubyte * 0),
    ]

    _payload_fields = [
        ('data', '{self.write_length}s', 0, bytes, serialize),
    ]

    index_group = _enum_property('_index_group',  # type: ignore
                                 constants.AdsIndexGroup,
                                 strict=False)
    _dict_mapping = {'_index_group': 'index_group'}

    def __init__(self, index_group: constants.AdsIndexGroup,
                 index_offset: int, data: typing.Any = None):
        if data is not None:
            data = bytes(data)
            length = len(data)
        else:
            data = None
            length = 0

        super().__init__(index_group, index_offset, length)
        self.data = data

    @property
    def handle(self) -> int:
        return self.index_offset


@use_for_request(constants.AdsCommandId.READ_WRITE)
class AdsReadWriteRequest(_AdsStructBase):
    """
    With ADS Read/Write `data` will be written to an ADS device. Data can also
    be read from the ADS device.

    SYM_HNDBYNAME = 0xF003
        The data which can be read are addressed by the Index Group and the
        Index Offset, or by way of symbol name (held in data).
    """
    index_group: constants.AdsIndexGroup
    index_offset: int
    read_length: int
    write_length: int
    _data_start: ctypes.c_ubyte
    data: typing.Any = None

    _fields_ = [
        ('_index_group', ctypes.c_uint32),
        ('index_offset', ctypes.c_uint32),
        ('read_length', ctypes.c_uint32),
        ('write_length', ctypes.c_uint32),
        ('_data_start', ctypes.c_ubyte * 0),
    ]

    _payload_fields = [
        ('data', '{self.write_length}s', 0, bytes, serialize),
    ]

    index_group = _enum_property('_index_group',  # type: ignore
                                 constants.AdsIndexGroup,
                                 strict=False)
    _dict_mapping = {'_index_group': 'index_group'}

    @classmethod
    def create_handle_by_name_request(cls, name: str) -> 'AdsReadWriteRequest':
        data = string_to_byte_string(name) + b'\x00'
        request = cls(constants.AdsIndexGroup.SYM_HNDBYNAME,
                      0, ctypes.sizeof(ctypes.c_uint32),
                      len(data))
        request.data = data
        return request

    @classmethod
    def create_info_by_name_request(cls, name: str) -> 'AdsReadWriteRequest':
        # Read length includes up to 3 T_MaxString strings:
        read_length = ctypes.sizeof(AdsSymbolEntry) + 3 * 256

        data = string_to_byte_string(name) + b'\x00'
        write_length = len(data)
        request = cls(constants.AdsIndexGroup.SYM_INFOBYNAMEEX, 0, read_length,
                      write_length)
        request.data = data
        return request


@use_for_request(constants.AdsCommandId.READ)
class AdsReadRequest(_AdsStructBase):
    # indexGroup of symbol: input, output etc.
    index_group: constants.AdsIndexGroup
    index_offset: int
    length: int

    _fields_ = [
        ('_index_group', ctypes.c_uint32),
        # indexOffset of symbol
        ('index_offset', ctypes.c_uint32),
        # Length of the data
        ('length', ctypes.c_uint32),
    ]

    index_group = _enum_property('_index_group',  # type: ignore
                                 constants.AdsIndexGroup,
                                 strict=False)
    _dict_mapping = {'_index_group': 'index_group'}

    @property
    def handle(self) -> int:
        return self.index_offset


class AmsTcpHeader(_AdsStructBase):
    reserved: int
    length: int

    _fields_ = [
        ('reserved', ctypes.c_uint16),
        ('length', ctypes.c_uint32),
    ]

    def __init__(self, length: int = 0):
        super().__init__(0, length)


class AoERequestHeader(_AdsStructBase):
    group: int
    offset: int
    length: int

    _fields_ = [
        ('index_group', ctypes.c_uint32),
        ('offset', ctypes.c_uint32),
        ('length', ctypes.c_uint32),
    ]

    @classmethod
    def from_sdo(cls,
                 sdo_index: int,
                 sdo_sub_index: int,
                 data_length: int) -> 'AoERequestHeader':
        """
        Create an AoERequestHeader given SDO settings.

        Parameters
        ----------
        sdo_index : int
        sdo_sub_index : int
        data_length : int

        Returns
        -------
        AoERequestHeader
        """
        return cls(constants.SDO_UPLOAD, (sdo_index) << 16 | sdo_sub_index,
                   data_length)


class AoEWriteRequestHeader(AoERequestHeader):
    write_length: int

    _fields_ = [
        # Inherits fields from AoERequestHeader.
        ('write_length', ctypes.c_uint32),
    ]


@use_for_response(constants.AdsCommandId.READ_STATE)
class AdsReadStateResponse(_AdsStructBase):
    ads_state: int
    dev_state: int

    _fields_ = [
        ('_ads_state', ctypes.c_uint16),
        ('dev_state', ctypes.c_uint16),
    ]

    ads_state = _enum_property('_ads_state',  # type: ignore
                               constants.AdsState)
    _dict_mapping = {'_ads_state': 'ads_state'}


@use_for_request(constants.AdsCommandId.WRITE_CONTROL)
class AdsWriteControlRequest(_AdsStructBase):
    _ads_state: int
    ads_state: constants.AdsState
    dev_state: int
    length: int
    data: bytes

    _fields_ = [
        ('_ads_state', ctypes.c_uint16),
        ('dev_state', ctypes.c_uint16),
        ('length', ctypes.c_uint32),
        ('_data_start', ctypes.c_ubyte * 0),
    ]
    _payload_fields = [
        ('data', '{self.write_length}s', 0, bytes, serialize),
    ]

    ads_state = _enum_property('_ads_state',  # type: ignore
                               constants.AdsState)
    _dict_mapping = {'_data_start': 'data',
                     '_ads_state': 'ads_state'}


@use_for_request(constants.AdsCommandId.ADD_DEVICE_NOTIFICATION)
class AdsAddDeviceNotificationRequest(_AdsStructBase):
    _index_group: int
    index_group: constants.AdsIndexGroup
    index_offset: int
    length: int
    mode: int
    max_delay: int
    cycle_time: ctypes.c_ubyte

    _fields_ = [
        ('_index_group', ctypes.c_uint32),
        ('index_offset', ctypes.c_uint32),
        ('length', ctypes.c_uint32),
        ('mode', ctypes.c_uint32),
        ('max_delay', ctypes.c_uint32),
        ('cycle_time', ctypes.c_uint32),
        ('reserved', ctypes.c_ubyte * 16),
    ]

    index_group = _enum_property('_index_group',  # type: ignore
                                 constants.AdsIndexGroup,
                                 strict=False)
    _dict_mapping = {'_index_group': 'index_group'}

    @property
    def handle(self) -> int:
        return self.index_offset


@use_for_request(constants.AdsCommandId.DEL_DEVICE_NOTIFICATION)
class AdsDeleteDeviceNotificationRequest(_AdsStructBase):
    handle: int
    _fields_ = [
        ('handle', ctypes.c_uint32),
    ]


class AoEHeader(_AdsStructBase):
    target: AmsAddr
    source: AmsAddr
    command_id: constants.AdsCommandId
    state_flags: constants.AoEHeaderFlag
    length: int
    error_code: int
    invoke_id: int

    _fields_ = [
        ('target', AmsAddr),
        ('source', AmsAddr),
        ('_command_id', ctypes.c_uint16),
        ('_state_flags', ctypes.c_uint16),
        ('length', ctypes.c_uint32),
        ('error_code', ctypes.c_uint32),
        ('invoke_id', ctypes.c_uint32),
    ]

    @property
    def is_response(self) -> bool:
        return (AoEHeaderFlag.RESPONSE in self.state_flags)

    @property
    def is_request(self) -> bool:
        return not self.is_response

    @classmethod
    def create_request(
            cls,
            target: AmsAddr,
            source: AmsAddr,
            command_id: constants.AdsCommandId,
            length: int,
            invoke_id: int, *,
            state_flags: constants.AoEHeaderFlag = AoEHeaderFlag.ADS_COMMAND,
            error_code: int = 0,
    ) -> 'AoEHeader':
        """Create a request header."""
        return cls(target, source, command_id, state_flags, length, error_code,
                   invoke_id)

    @classmethod
    def create_response(
            cls,
            target: AmsAddr,
            source: AmsAddr,
            command_id: constants.AdsCommandId,
            length: int,
            invoke_id: int, *,
            state_flags: AoEHeaderFlag = (AoEHeaderFlag.ADS_COMMAND |
                                          AoEHeaderFlag.RESPONSE),
            error_code: int = 0,
    ) -> 'AoEHeader':
        """Create a response header."""
        return cls(target, source, command_id, state_flags, length, error_code,
                   invoke_id)

    command_id = _enum_property('_command_id', constants.AdsCommandId)
    state_flags = _enum_property(
        '_state_flags', constants.AoEHeaderFlag)
    _dict_mapping = {'_command_id': 'command_id',
                     '_state_flags': 'state_flags'}


@use_for_response(constants.AdsCommandId.WRITE_CONTROL)
@use_for_response(constants.AdsCommandId.INVALID)
@use_for_response(constants.AdsCommandId.DEL_DEVICE_NOTIFICATION)
@use_for_response(constants.AdsCommandId.WRITE)
# @use_for_response(constants.AdsCommandId.DEVICE_NOTIFICATION)
class AoEResponseHeader(_AdsStructBase):
    _fields_ = [
        ('_result', ctypes.c_uint32),
    ]

    result = _enum_property('_result', constants.AdsError)
    _dict_mapping = {'_result': 'result'}

    def __init__(self, result: constants.AdsError = constants.AdsError.NOERR):
        super().__init__(result)


@use_for_response(constants.AdsCommandId.READ)
class AoEReadResponse(AoEResponseHeader):
    _fields_ = [
        # Inherits 'result' from AoEResponseHeader
        ('read_length', ctypes.c_uint32),  # type: ignore
        ('_data_start', ctypes.c_uint8 * 0),
    ]

    _payload_fields = [
        ('data', '{self.read_length}s', 0, bytes, serialize),
    ]

    _dict_mapping = {'_data_start': 'data'}

    def __init__(self, *,
                 result: constants.AdsError = constants.AdsError.NOERR,
                 length: int = 0,
                 data: typing.Any = None,
                 ):
        super().__init__(result=result)
        self.read_length = length
        self.data = data

    def serialize(self) -> bytearray:
        # TODO: double serialization
        self.read_length = len(serialize(self.data))
        return super().serialize()


@use_for_response(constants.AdsCommandId.READ_WRITE)
class AoEHandleResponse(AoEResponseHeader):
    _fields_ = [
        # Inherits 'result' from AoEResponseHeader
        ('length', ctypes.c_uint32),
        ('handle', ctypes.c_uint32),
    ]

    def __init__(self, *,
                 result: constants.AdsError = constants.AdsError.NOERR,
                 handle: int = 0,
                 ):
        super().__init__(result)
        self.length = ctypes.sizeof(ctypes.c_uint32)
        self.handle = handle


@use_for_response(constants.AdsCommandId.ADD_DEVICE_NOTIFICATION)
class AoENotificationHandleResponse(AoEResponseHeader):
    _fields_ = [
        # Inherits 'result' from AoEResponseHeader
        ('handle', ctypes.c_uint32),
    ]

    def __init__(self, *,
                 result: constants.AdsError = constants.AdsError.NOERR,
                 handle: int = 0,
                 ):
        super().__init__(result)
        self.handle = handle


def serialize_data(data_type: constants.AdsDataType, data: typing.Any,
                   length: int = None,
                   *, endian='<') -> typing.Tuple[int, bytes]:
    length = length if length is not None else len(data)
    ctypes_type = data_type.ctypes_type._type_  # type: ignore
    st = struct.Struct(f'{endian}{length}{ctypes_type}')
    return st.size, st.pack(data)


def deserialize_data(data_type: constants.AdsDataType,
                     length: int,
                     data: bytes,
                     *, endian='<') -> typing.Tuple[int, typing.Any]:
    ctypes_type = data_type.ctypes_type._type_  # type: ignore
    st = struct.Struct(f'{endian}{length}{ctypes_type}')
    return st.size, st.unpack(data)
