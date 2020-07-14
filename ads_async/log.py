'''
Logging configuration and adapters.

Adapted from caproto.
'''

import fnmatch
import logging
import sys

__all__ = ('configure', 'get_handler', 'SymbolFilter', 'AddressFilter',
           'RoleFilter', 'LogFormatter')


PLAIN_LOG_FORMAT = (
    "[%(levelname)1.1s %(asctime)s.%(msecs)03d %(module)12s:%(lineno)5d] "
    "%(message)s"
)
current_handler = None


class ComposableLogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        # The logging.LoggerAdapter siliently ignores `extra` in this usage:
        # log_adapter.debug(msg, extra={...})
        # and passes through log_adapater.extra instead. This subclass merges
        # the extra passed via keyword argument with the extra in the
        # attribute, giving precedence to the keyword argument.
        kwargs["extra"] = {**self.extra, **kwargs.get('extra', {})}
        return msg, kwargs


class LogFormatter(logging.Formatter):
    """
    Log formatter for ads-async.

    Key features of this formatter are:

    * Timestamps on every log line.
    * Includes extra record attributes (symbol, our_address, their_address,
      direction, role) when present.

    """
    DEFAULT_FORMAT = PLAIN_LOG_FORMAT
    DEFAULT_DATE_FORMAT = '%y%m%d %H:%M:%S'

    def __init__(self, fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATE_FORMAT):
        """
        Parameters
        ----------
        fmt : str, optional
            Log message format.

        datefmt : str, optional
            Datetime format.
            Used for formatting ``(asctime)`` placeholder in ``prefix_fmt``.
        """
        super().__init__(datefmt=datefmt)
        self._fmt = fmt

    def format(self, record):
        message = []
        if hasattr(record, 'our_address'):
            message.append('%s:%d' % record.our_address)
        if hasattr(record, 'direction'):
            message.append('%s' % record.direction)
        if hasattr(record, 'their_address'):
            message.append('%s:%d' % record.their_address)
        if hasattr(record, 'bytesize'):
            message.append('%dB' % record.bytesize)
        if hasattr(record, 'counter'):
            message.append('(%d of %d)' % record.counter)
        if hasattr(record, 'sequence'):
            message.append('{%d}' % record.sequence)
        if hasattr(record, 'symbol'):
            message.append(record.symbol)
        message.append(record.getMessage())
        record.message = ' '.join(message)
        record.asctime = self.formatTime(record, self.datefmt)

        formatted = self._fmt % record.__dict__

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            formatted = '{}\n{}'.format(formatted.rstrip(), record.exc_text)
        return formatted.replace("\n", "\n    ")


def validate_level(level) -> int:
    '''Return an integer for level comparison.'''
    if isinstance(level, int):
        return level

    if isinstance(level, str):
        levelno = logging.getLevelName(level)
        if isinstance(levelno, int):
            return levelno

    raise ValueError("Level is not a valid python logging string")


class SymbolFilter(logging.Filter):
    '''
    Block any message that is lower than certain level except it related to
    target symbols. You have option to choose whether env, config and misc
    message is exclusive or not.

    Parameters
    ----------
    names : string or list of string
        Symbol list which will be filtered in.

    level : str or int
        Represents the "barrier height" of this filter: any records with a
        levelno greater than or equal to this will always pass through. Default
        is 'WARNING'. Python log level names or their corresponding integers
        are accepted.

    exclusive : bool
        If True, records for which this filter is "not applicable" (i.e. the
        relevant extra context is not present) will be blocked. False by
        default.

    Returns
    -------
    passes : bool
    '''
    def __init__(self, *names, level='WARNING', exclusive=False):
        self.names = names
        self.levelno = validate_level(level)
        self.exclusive = exclusive

    def filter(self, record):
        if record.levelno >= self.levelno:
            return True
        elif hasattr(record, 'symbol'):
            for name in self.names:
                if fnmatch.fnmatch(record.symbol, name):
                    return True
            return False
        else:
            return not self.exclusive


class AddressFilter(logging.Filter):
    '''
    Block any message that is lower than certain level except it related to
    target addresses. You have option to choose whether env, config and misc
    message is exclusive or not.

    Parameters
    ----------
    addresses_list : list of address in form of (host_str, port_val)
        Addresses list which will be filtered in.

    level : str or int
        Represents the "barrier height" of this filter: any records with a
        levelno greater than or equal to this will always pass through. Default
        is 'WARNING'. Python log level names or their corresponding integers
        are accepted.

    exclusive : bool
        If True, records for which this filter is "not applicable" (i.e. the
        relevant extra context is not present) will be blocked. False by
        default.

    Returns
    -------
    passes : bool
    '''

    def __init__(self, *addresses_list, level='WARNING', exclusive=False):
        self.addresses_list = []
        self.hosts_list = []
        for address in addresses_list:
            if isinstance(address, str):
                if ':' in address:
                    host, port_as_str = address.split(':')
                    self.addresses_list.append((host, int(port_as_str)))
                else:
                    self.hosts_list.append(address)
            elif isinstance(address, tuple):
                if len(address) == 2:
                    self.addresses_list.append(address)
                else:
                    raise ValueError(
                        "The target addresses should given as strings like "
                        "'XX.XX.XX.XX:YYYY' or tuples like ('XX.XX.XX.XX', "
                        "YYYY).")
            else:
                raise ValueError(
                    "The target addresses should given as strings like "
                    "'XX.XX.XX.XX:YYYY' or tuples like ('XX.XX.XX.XX', YYYY).")

        self.levelno = validate_level(level)
        self.exclusive = exclusive

    def filter(self, record):
        if record.levelno >= self.levelno:
            return True
        elif hasattr(record, 'our_address'):
            return (record.our_address in self.addresses_list or
                    record.our_address[0] in self.hosts_list or
                    record.their_address in self.addresses_list or
                    record.their_address[0] in self.hosts_list)
        else:
            return not self.exclusive


class RoleFilter(logging.Filter):
    '''
    Block any message that is lower than certain level except it related to
    target role.

    Parameters
    ----------
    role : 'CLIENT' or 'SERVER'
        Role of the local machine.

    level : str or int
        Represents the "barrier height" of this filter: any records with a
        levelno greater than or equal to this will always pass through. Default
        is 'WARNING'. Python log level names or their corresponding integers
        are accepted.

    exclusive : bool
        If True, records for which this filter is "not applicable" (i.e. the
        relevant extra context is not present) will be blocked. False by
        default.

    Returns
    -------
    passes: bool
    '''
    def __init__(self, role, level='WARNING', exclusive=False):
        self.role = role
        self.levelno = validate_level(level)
        self.exclusive = exclusive

    def filter(self, record):
        if record.levelno >= self.levelno:
            return True
        elif hasattr(record, 'role'):
            return record.role is self.role
        else:
            return not self.exclusive


def _set_handler_with_logger(logger_name='ads_async', file=sys.stdout,
                             datefmt='%H:%M:%S', level='WARNING'):
    if isinstance(file, str):
        handler = logging.FileHandler(file)
    else:
        handler = logging.StreamHandler(file)
    levelno = validate_level(level)
    handler.setLevel(levelno)
    handler.setFormatter(
        LogFormatter(PLAIN_LOG_FORMAT, datefmt=datefmt))
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    if logger.getEffectiveLevel() > levelno:
        logger.setLevel(levelno)


def configure(file=sys.stdout, datefmt='%H:%M:%S', level='WARNING'):
    """
    Set a new handler on the ``logging.getLogger('ads_async')`` logger.

    If this is called more than once, the handler from the previous invocation
    is removed (if still present) and replaced.

    Parameters
    ----------
    file : object with ``write`` method or filename string
        Default is ``sys.stdout``.
    datefmt : string
        Date format. Default is ``'%H:%M:%S'``.
    level : str or int
        Python logging level, given as string or corresponding integer.
        Default is 'WARNING'.

    Returns
    -------
    handler : logging.Handler
        The handler, which has already been added to the 'ads_async' logger.

    Examples
    --------
    Log to a file.

    >>> configure(file='/tmp/what_is_happening.txt')

    Include the date along with the time. (The log messages will always include
    microseconds, which are configured separately, not as part of 'datefmt'.)

    >>> configure(datefmt="%Y-%m-%d %H:%M:%S")

    Increase verbosity: show level INFO or higher.

    >>> configure(level='INFO')
    """
    global current_handler
    if isinstance(file, str):
        handler = logging.FileHandler(file)
    else:
        handler = logging.StreamHandler(file)

    levelno = validate_level(level)
    handler.setLevel(levelno)
    handler.setFormatter(LogFormatter(PLAIN_LOG_FORMAT, datefmt=datefmt))
    logger = logging.getLogger('ads_async')
    if current_handler in logger.handlers:
        logger.removeHandler(current_handler)

    logger.addHandler(handler)
    current_handler = handler
    if logger.getEffectiveLevel() > levelno:
        logger.setLevel(levelno)
    return handler


def get_handler():
    """
    Return the handler configured by the most recent call to
    :func:`configure`.

    If :func:`configure` has not yet been called, this returns ``None``.
    """
    return current_handler
