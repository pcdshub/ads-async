import ctypes
import enum
import ipaddress
import typing


ADS_TCP_SERVER_PORT = 0xBF02


class AmsPort(enum.IntEnum):
    LOGGER = 100
    R0_RTIME = 200
    R0_TRACE = (R0_RTIME + 90)
    R0_IO = 300
    R0_SPS = 400
    R0_NC = 500
    R0_ISG = 550
    R0_PCS = 600
    R0_PLC = 801
    R0_PLC_RTS1 = 801
    R0_PLC_RTS2 = 811
    R0_PLC_RTS3 = 821
    R0_PLC_RTS4 = 831
    R0_PLC_TC3 = 851

    @property
    def tcp_port(self):
        return ADS_TCP_SERVER_PORT + self.value


class AdsCommandId(enum.IntEnum):
    INVALID = 0x00
    READDEVICEINFO = 0x01
    READ = 0x02
    WRITE = 0x03
    READSTATE = 0x04
    WRITECTRL = 0x05
    ADDDEVICENOTE = 0x06
    DELDEVICENOTE = 0x07
    DEVICENOTE = 0x08
    READWRITE = 0x09


class AdsIndexGroup(enum.IntEnum):
    # ADS reserved index groups
    SYMTAB = 0xF000
    SYMNAME = 0xF001
    SYMVAL = 0xF002

    SYM_HNDBYNAME = 0xF003
    SYM_VALBYNAME = 0xF004
    SYM_VALBYHND = 0xF005
    SYM_RELEASEHND = 0xF006
    SYM_INFOBYNAME = 0xF007
    SYM_VERSION = 0xF008
    SYM_INFOBYNAMEEX = 0xF009

    SYM_DOWNLOAD = 0xF00A
    SYM_UPLOAD = 0xF00B
    SYM_UPLOADINFO = 0xF00C
    SYM_DOWNLOAD2 = 0xF00D
    SYM_DT_UPLOAD = 0xF00E
    SYM_UPLOADINFO2 = 0xF00F

    # notification of named handle
    SYMNOTE = 0xF010

    # AdsRW  IOffs list size or 0 (=0 -> list size == WLength/3*sizeof(ULONG))
    # @param W: {list of IGrp, IOffs, Length}
    # @param R: if IOffs != 0 then {list of results} and {list of data}
    # @param R: if IOffs == 0 then only data (sum result)
    SUMUP_READ = 0xF080

    # AdsRW  IOffs list size
    # @param W: {list of IGrp, IOffs, Length} followed by {list of data}
    # @param R: list of results
    SUMUP_WRITE = 0xF081

    # AdsRW  IOffs list size
    # @param W: {list of IGrp, IOffs, RLength, WLength} followed by {list of data}
    # @param R: {list of results, RLength} followed by {list of data}
    SUMUP_READWRITE = 0xF082

    # AdsRW  IOffs list size
    # @param W: {list of IGrp, IOffs, Length}
    SUMUP_READEX = 0xF083

    # AdsRW  IOffs list size
    # @param W: {list of IGrp, IOffs, Length}
    # @param R: {list of results, Length} followed by {list of data (returned lengths)}
    SUMUP_READEX2 = 0xF084

    # AdsRW  IOffs list size
    # @param W: {list of IGrp, IOffs, Attrib}
    # @param R: {list of results, handles}
    SUMUP_ADDDEVNOTE = 0xF085

    # AdsRW  IOffs list size
    # @param W: {list of handles}
    # @param R: {list of results, Length} followed by {list of data}
    SUMUP_DELDEVNOTE = 0xF086

    # read/write input byte(s)
    IOIMAGE_RWIB = 0xF020
    # read/write input bit
    IOIMAGE_RWIX = 0xF021
    # read input size (in byte)
    IOIMAGE_RISIZE = 0xF025
    # read/write output byte(s)
    IOIMAGE_RWOB = 0xF030
    # read/write output bit
    IOIMAGE_RWOX = 0xF031
    # read output size (in byte)
    IOIMAGE_ROSIZE = 0xF035
    # write inputs to null
    IOIMAGE_CLEARI = 0xF040
    # write outputs to null
    IOIMAGE_CLEARO = 0xF050
    # read input and write output byte(s)
    IOIMAGE_RWIOB = 0xF060

    # state, name, etc...
    DEVICE_DATA = 0xF100


ADSIOFFS_DEVDATA_ADSSTATE = 0x0000      # ads state of device
ADSIOFFS_DEVDATA_DEVSTATE = 0x0002      # device state

# Error code base values:
# Global Return codes
ERR_GLOBAL = 0x0000
# Router Return codes
ERR_ROUTER = 0x0500
# ADS Return codes
ERR_ADSERRS = 0x0700


class AdsError(enum.IntEnum):
    NOERR = 0x00

    # target port not found, possibly the ADS Server is not started
    GLOBALERR_TARGET_PORT = (0x06 + ERR_GLOBAL)
    # target machine not found, possibly missing ADS routes
    GLOBALERR_MISSING_ROUTE = (0x07 + ERR_GLOBAL)
    # No memory
    GLOBALERR_NO_MEMORY = (0x19 + ERR_GLOBAL)
    # TCP send error
    GLOBALERR_TCP_SEND = (0x1A + ERR_GLOBAL)

    # The desired port number is already assigned
    ROUTERERR_PORTALREADYINUSE = (0x06 + ERR_ROUTER)
    # Port not registered
    ROUTERERR_NOTREGISTERED = (0x07 + ERR_ROUTER)
    # The maximum number of Ports reached
    ROUTERERR_NOMOREQUEUES = (0x08 + ERR_ROUTER)

    # Error class < device error >
    DEVICE_ERROR = (0x00 + ERR_ADSERRS)
    # Service is not supported by server
    DEVICE_SRVNOTSUPP = (0x01 + ERR_ADSERRS)
    # invalid indexGroup
    DEVICE_INVALIDGRP = (0x02 + ERR_ADSERRS)
    # invalid indexOffset
    DEVICE_INVALIDOFFSET = (0x03 + ERR_ADSERRS)
    # reading/writing not permitted
    DEVICE_INVALIDACCESS = (0x04 + ERR_ADSERRS)
    # parameter size not correct
    DEVICE_INVALIDSIZE = (0x05 + ERR_ADSERRS)
    # invalid parameter value(s)
    DEVICE_INVALIDDATA = (0x06 + ERR_ADSERRS)
    # device is not in a ready state
    DEVICE_NOTREADY = (0x07 + ERR_ADSERRS)
    # device is busy
    DEVICE_BUSY = (0x08 + ERR_ADSERRS)
    # invalid context (must be InWindows)
    DEVICE_INVALIDCONTEXT = (0x09 + ERR_ADSERRS)
    # out of memory
    DEVICE_NOMEMORY = (0x0A + ERR_ADSERRS)
    # invalid parameter value(s)
    DEVICE_INVALIDPARM = (0x0B + ERR_ADSERRS)
    # not found (files, ...)
    DEVICE_NOTFOUND = (0x0C + ERR_ADSERRS)
    # syntax error in comand or file
    DEVICE_SYNTAX = (0x0D + ERR_ADSERRS)
    # objects do not match
    DEVICE_INCOMPATIBLE = (0x0E + ERR_ADSERRS)
    # object already exists
    DEVICE_EXISTS = (0x0F + ERR_ADSERRS)
    # symbol not found
    DEVICE_SYMBOLNOTFOUND = (0x10 + ERR_ADSERRS)
    # symbol version invalid, possibly caused by an 'onlinechange' -> try to
    # release handle and get a new one
    DEVICE_SYMBOLVERSIONINVALID = (0x11 + ERR_ADSERRS)
    # server is in invalid state
    DEVICE_INVALIDSTATE = (0x12 + ERR_ADSERRS)
    # AdsTransMode not supported
    DEVICE_TRANSMODENOTSUPP = (0x13 + ERR_ADSERRS)
    # Notification handle is invalid, possibly caussed by an 'onlinechange' ->
    # try to release handle and get a new one
    DEVICE_NOTIFYHNDINVALID = (0x14 + ERR_ADSERRS)
    # Notification client not registered
    DEVICE_CLIENTUNKNOWN = (0x15 + ERR_ADSERRS)
    # no more notification handles
    DEVICE_NOMOREHDLS = (0x16 + ERR_ADSERRS)
    # size for watch to big
    DEVICE_INVALIDWATCHSIZE = (0x17 + ERR_ADSERRS)
    # device not initialized
    DEVICE_NOTINIT = (0x18 + ERR_ADSERRS)
    # device has a timeout
    DEVICE_TIMEOUT = (0x19 + ERR_ADSERRS)
    # query interface failed
    DEVICE_NOINTERFACE = (0x1A + ERR_ADSERRS)
    # wrong interface required
    DEVICE_INVALIDINTERFACE = (0x1B + ERR_ADSERRS)
    # class ID is invalid
    DEVICE_INVALIDCLSID = (0x1C + ERR_ADSERRS)
    # object ID is invalid
    DEVICE_INVALIDOBJID = (0x1D + ERR_ADSERRS)
    # request is pending
    DEVICE_PENDING = (0x1E + ERR_ADSERRS)
    # request is aborted
    DEVICE_ABORTED = (0x1F + ERR_ADSERRS)
    # signal warning
    DEVICE_WARNING = (0x20 + ERR_ADSERRS)
    # invalid array index
    DEVICE_INVALIDARRAYIDX = (0x21 + ERR_ADSERRS)
    # symbol not active, possibly caussed by an 'onlinechange' -> try to
    # release handle and get a new one
    DEVICE_SYMBOLNOTACTIVE = (0x22 + ERR_ADSERRS)
    # access denied
    DEVICE_ACCESSDENIED = (0x23 + ERR_ADSERRS)
    # no license found -> Activate license for TwinCAT 3 function
    DEVICE_LICENSENOTFOUND = (0x24 + ERR_ADSERRS)
    # license expired
    DEVICE_LICENSEEXPIRED = (0x25 + ERR_ADSERRS)
    # license exceeded
    DEVICE_LICENSEEXCEEDED = (0x26 + ERR_ADSERRS)
    # license invalid
    DEVICE_LICENSEINVALID = (0x27 + ERR_ADSERRS)
    # license invalid system id
    DEVICE_LICENSESYSTEMID = (0x28 + ERR_ADSERRS)
    # license not time limited
    DEVICE_LICENSENOTIMELIMIT = (0x29 + ERR_ADSERRS)
    # license issue time in the future
    DEVICE_LICENSEFUTUREISSUE = (0x2A + ERR_ADSERRS)
    # license time period to long
    DEVICE_LICENSETIMETOLONG = (0x2B + ERR_ADSERRS)
    # exception in device specific code -> Check each device transistions
    DEVICE_EXCEPTION = (0x2C + ERR_ADSERRS)
    # license file read twice
    DEVICE_LICENSEDUPLICATED = (0x2D + ERR_ADSERRS)
    # invalid signature
    DEVICE_SIGNATUREINVALID = (0x2E + ERR_ADSERRS)
    # public key certificate
    DEVICE_CERTIFICATEINVALID = (0x2F + ERR_ADSERRS)
    # Error class < client error >
    CLIENT_ERROR = (0x40 + ERR_ADSERRS)
    # invalid parameter at service call
    CLIENT_INVALIDPARM = (0x41 + ERR_ADSERRS)
    # polling list	is empty
    CLIENT_LISTEMPTY = (0x42 + ERR_ADSERRS)
    # var connection already in use
    CLIENT_VARUSED = (0x43 + ERR_ADSERRS)
    # invoke id in use
    CLIENT_DUPLINVOKEID = (0x44 + ERR_ADSERRS)
    # timeout elapsed -> Check ADS routes of sender and receiver and your
    # [firewall
    # setting](http://infosys.beckhoff.com/content/1033/tcremoteaccess/html/tcremoteaccess_firewall.html?id=12027)
    CLIENT_SYNCTIMEOUT = (0x45 + ERR_ADSERRS)
    # error in win32 subsystem
    CLIENT_W32ERROR = (0x46 + ERR_ADSERRS)
    # Invalid client timeout value
    CLIENT_TIMEOUTINVALID = (0x47 + ERR_ADSERRS)
    # ads dll
    CLIENT_PORTNOTOPEN = (0x48 + ERR_ADSERRS)
    # ads dll
    CLIENT_NOAMSADDR = (0x49 + ERR_ADSERRS)
    # internal error in ads sync
    CLIENT_SYNCINTERNAL = (0x50 + ERR_ADSERRS)
    # hash table overflow
    CLIENT_ADDHASH = (0x51 + ERR_ADSERRS)
    # key not found in hash table
    CLIENT_REMOVEHASH = (0x52 + ERR_ADSERRS)
    # no more symbols in cache
    CLIENT_NOMORESYM = (0x53 + ERR_ADSERRS)
    # invalid response received
    CLIENT_SYNCRESINVALID = (0x54 + ERR_ADSERRS)
    # sync port is locked
    CLIENT_SYNCPORTLOCKED = (0x55 + ERR_ADSERRS)


class AdsDataType(enum.IntEnum):
    VOID = 0
    INT8 = 16
    UINT8 = 17
    INT16 = 2
    UINT16 = 18
    INT32 = 3
    UINT32 = 19
    INT64 = 20
    UINT64 = 21
    REAL32 = 4
    REAL64 = 5
    BIGTYPE = 65
    STRING = 30
    WSTRING = 31
    REAL80 = 32
    BIT = 33
    MAXTYPES = 34


AdsDataType.to_ctypes = {
    # AdsDataType.VOID: None,
    AdsDataType.INT8: ctypes.c_int8,
    AdsDataType.UINT8: ctypes.c_uint8,
    AdsDataType.INT16: ctypes.c_int16,
    AdsDataType.UINT16: ctypes.c_uint16,
    AdsDataType.INT32: ctypes.c_int32,
    AdsDataType.UINT32: ctypes.c_uint32,
    AdsDataType.INT64: ctypes.c_int64,
    AdsDataType.UINT64: ctypes.c_uint64,
    AdsDataType.REAL32: ctypes.c_float,
    AdsDataType.REAL64: ctypes.c_double,
    # AdsDataType.BIGTYPE: None,
    AdsDataType.STRING: ctypes.c_char,
    AdsDataType.WSTRING: ctypes.c_wchar,
    # AdsDataType.REAL80
    AdsDataType.BIT: ctypes.c_bool,
}


class AdsTransmissionMode(enum.IntEnum):
    NOTRANS = 0
    CLIENTCYCLE = 1
    CLIENTONCHA = 2
    SERVERCYCLE = 3
    SERVERONCHA = 4
    SERVERCYCLE2 = 5
    SERVERONCHA2 = 6
    CLIENT1REQ = 10
    MAXMODES = 11


class AdsState(enum.IntEnum):  # uint16
    INVALID = 0
    IDLE = 1
    RESET = 2
    INIT = 3
    START = 4
    RUN = 5
    STOP = 6
    SAVECFG = 7
    LOADCFG = 8
    POWERFAILURE = 9
    POWERGOOD = 10
    ERROR = 11
    SHUTDOWN = 12
    SUSPEND = 13
    RESUME = 14
    CONFIG = 15
    RECONFIG = 16
    STOPPING = 17
    INCOMPATIBLE = 18
    EXCEPTION = 19
    MAXSTATES = 20


class AdsSymbolFlag(enum.IntFlag):
    PERSISTENT = (1 << 0)
    BITVALUE = (1 << 1)
    REFERENCETO = (1 << 2)
    TYPEGUID = (1 << 3)
    TCCOMIFACEPTR = (1 << 4)
    READONLY = (1 << 5)
    CONTEXTMASK = (0xF00)


class _AdsStructBase(ctypes.LittleEndianStructure):
    _pack_ = 1

    def to_dict(self):
        """Return the structure as a dictionary."""
        return {attr: getattr(self, attr)
                for attr, *info in self._fields_}

    def __repr__(self):
        formatted_args = ", ".join(f"{k!s}={v!r}"
                                   for k, v in self.to_dict().items())
        return f"{self.__class__.__name__}({formatted_args})"


def _create_enum_property(field_name: str,
                          enum_cls: enum.Enum,
                          *,
                          doc: str = None,
                          strict: bool = True):
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


class AmsNetId(_AdsStructBase):
    """
    The NetId of and ADS device can be represented in this structure.

    Net IDs are do not necessarily have to have a relation to an IP address,
    though by convention it may be wise to configure them similarly.
    """
    _fields_ = [
        ('octets', ctypes.c_uint8 * 6),
    ]

    def __repr__(self):
        return '.'.join(str(c) for c in self.octets)

    @classmethod
    def from_ipv4(cls, ip: typing.Union[str, ipaddress.IPv4Address],
                  octet5: int = 1,
                  octet6: int = 1):
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
    def from_string(cls, addr: str):
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
    _fields_ = [
        ('net_id', AmsNetId),

        # AMS Port number
        ('_port', ctypes.c_uint16),
    ]

    port = _create_enum_property('port', AmsPort, strict=False)

    def __repr__(self):
        return f'{self.net_id}:{self.port.value}({self.port.name})'


class AdsVersion(_AdsStructBase):
    """Contains the version number, revision number and build number."""

    _fields_ = [
        ('version', ctypes.c_uint8),
        ('revision', ctypes.c_uint8),
        ('build', ctypes.c_uint8),
    ]


class AdsNotificationAttrib(_AdsStructBase):
    """
    Contains all the attributes for the definition of a notification.

    The ADS DLL is buffered from the real time transmission by a FIFO.
    TwinCAT first writes every value that is to be transmitted by means
    of the callback function into the FIFO. If the buffer is full, or if
    the nMaxDelay time has elapsed, then the callback function is invoked
    for each entry. The nTransMode parameter affects this process as follows:

    @par ADSTRANS_SERVERCYCLE
    The value is written cyclically into the FIFO at intervals of
    nCycleTime. The smallest possible value for nCycleTime is the cycle
    time of the ADS server; for the PLC, this is the task cycle time.
    The cycle time can be handled in 1ms steps. If you enter a cycle time
    of 0 ms, then the value is written into the FIFO with every task cycle.

    @par ADSTRANS_SERVERONCHA
    A value is only written into the FIFO if it has changed. The real-time
    sampling is executed in the time given in nCycleTime. The cycle time
    can be handled in 1ms steps. If you enter 0 ms as the cycle time, the
    variable is written into the FIFO every time it changes.

    Warning: Too many read operations can load the system so heavily that
    the user interface becomes much slower.

    Tip: Set the cycle time to the most appropriate values, and always
    close connections when they are no longer required.
    """

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

    transmission_mode = _create_enum_property(
        '_transmission_mode', AdsTransmissionMode,
        doc='Transmission mode settings (see AdsTransmissionMode)',
    )


class AdsNotificationHeader(_AdsStructBase):
    """This structure is also passed to the callback function."""

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
#  @brief Type definition of the callback function required by the AdsSyncAddDeviceNotificationReqEx() function.
#  @param[in] pAddr Structure with NetId and port number of the ADS server.
#  @param[in] pNotification pointer to a AdsNotificationHeader structure
#  @param[in] hUser custom handle pass to AdsSyncAddDeviceNotificationReqEx() during registration
# /
# typedef void (* PAdsNotificationFuncEx)(const AmsAddr* pAddr, const AdsNotificationHeader* pNotification,
#                                         uint32_t hUser);


class AdsSymbolEntry(_AdsStructBase):
    """
    This structure describes the header of ADS symbol information

    Calling AdsSyncReadWriteReqEx2 with IndexGroup == ADSIGRP_SYM_INFOBYNAMEEX
    will return ADS symbol information in the provided readData buffer.
    The header of that information is structured as AdsSymbolEntry and can
    be followed by zero terminated strings for "symbol name", "type name"
    and a "comment"
    """

    _fields_ = [
        # length of complete symbol entry
        ('entry_length', ctypes.c_uint32),
        # indexGroup of symbol: input, output etc.
        ('index_group', ctypes.c_uint32),
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
    ]

    flags = _create_enum_property('_flags', AdsSymbolFlag)
    data_type = _create_enum_property('_data_type', AdsDataType)


class AdsSymbolInfoByName(_AdsStructBase):
    """Used to provide ADS symbol information for ADS SUM commands."""

    _fields_ = [
        # indexGroup of symbol: input, output etc.
        ('index_group', ctypes.c_uint32),
        # indexOffset of symbol
        ('index_offset', ctypes.c_uint32),
        # Length of the data
        ('length', ctypes.c_uint32),
    ]
