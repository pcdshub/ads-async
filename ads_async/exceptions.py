from .constants import AdsError
from .structs import T_AdsStructure

# if typing.TYPE_CHECKING:


class AdsAsyncException(Exception):
    ...


class RequestFailedError(AdsAsyncException):
    code: AdsError
    reason: str

    def __init__(self, reason: str, code: "AdsError", request: "T_AdsStructure"):
        super().__init__(reason)
        self.code = code
        self.request = request

    def __repr__(self):
        return f"<ErrorResponse {self.code} ({self})>"


# class RequestFailedError(AdsAsyncException):
#     def __init__(
#         self,
#         reason: str,
#         request: 'T_AdsStructure',
#         result: 'T_AdsStructure',
#     ):
#         super().__init__(reason)
#         self.request = request
#         self.result = result

#     def __repr__(self):
#         return (
#             f'<RequestFailedError {self.request} got {self.result} ({self})>'
#         )
