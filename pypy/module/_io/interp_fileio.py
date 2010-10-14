from pypy.module._io.interp_io import W_RawIOBase
from pypy.interpreter.typedef import TypeDef
from pypy.interpreter.gateway import interp2app, unwrap_spec, Arguments
from pypy.interpreter.baseobjspace import ObjSpace, W_Root
from pypy.interpreter.error import OperationError, wrap_oserror2
import os

def _bad_mode(space):
    raise OperationError(space.w_ValueError, space.wrap(
        "Must have exactly one of read/write/append mode"))

def decode_mode(spac, mode):
    flags = 0
    rwa = False
    readable = False
    writable = False
    append = False
    plus = False

    for s in mode:
        if s == 'r':
            if rwa:
                _bad_mode(space)
            rwa = True
            readable = True
        elif s == 'w':
            if rwa:
                _bad_mode(space)
            rwa = True
            writable = True
            flags |= os.O_CREAT | os.O_TRUNC
        elif s == 'a':
            if rwa:
                _bad_mode(space)
            rwa = 1
            writable = True
            flags |= os.O_CREAT
            append = True
        elif s == 'b':
            pass
        elif s == '+':
            if plus:
                _bad_mode(space)
            readable = writable = True
            plus = True
        else:
            raise OperationError(space.w_ValueError, space.wrap(
                "invalid mode: %s" % (mode,)))

    if not rwa:
        _bad_mode(space)

    if readable and writable:
        flags |= os.O_RDWR
    elif readable:
        flags |= os.O_RDONLY
    else:
        flags |= os.O_WRONLY

    if hasattr(os, 'O_BINARY'):
        flags |= os.O_BINARY

    if hasattr(os, 'O_APPEND') and append:
        flags |= os.O_APPEND

    return readable, writable, flags

class W_FileIO(W_RawIOBase):
    def __init__(self, space):
        W_RawIOBase.__init__(self, space)
        self.fd = -1
        self.readable = False
        self.writable = False
        self.seekable = -1
        self.closefd = True

    @unwrap_spec(ObjSpace, W_Root, Arguments)
    def descr_new(space, w_subtype, __args__):
        self = space.allocate_instance(W_FileIO, w_subtype)
        W_FileIO.__init__(self, space)
        return space.wrap(self)

    @unwrap_spec('self', ObjSpace, W_Root, str, int)
    def descr_init(self, space, w_name, mode, closefd):
        if space.isinstance_w(w_name, space.w_float):
            raise OperationError(space.w_TypeError, space.wrap(
                "integer argument expected, got float"))
        try:
            fd = space.int_w(w_name)
        except OperationError, e:
            pass
        else:
            if fd < 0:
                raise OperationError(space.w_ValueError, space.wrap(
                    "negative file descriptor"))

        self.readable, self.writable, flags = decode_mode(space, mode)

        from pypy.module.posix.interp_posix import dispatch_filename, rposix
        try:
            self.fd = dispatch_filename(rposix.open)(
                space, w_name, flags, 0666)
        except OSError, e:
            raise wrap_oserror2(space, e, w_fname)
        self.closefd = bool(closefd)

    def _check_closed(self, space):
        if self.fd < 0:
            raise OperationError(space.w_ValueError, space.wrap(
                "I/O operation on closed file"))

    @unwrap_spec('self', ObjSpace)
    def readable_w(self, space):
        self._check_closed(space)
        return space.wrap(self.readable)

    @unwrap_spec('self', ObjSpace)
    def writable_w(self, space):
        self._check_closed(space)
        return space.wrap(self.writable)

W_FileIO.typedef = TypeDef(
    'FileIO', W_RawIOBase.typedef,
    __new__  = interp2app(W_FileIO.descr_new.im_func),
    __init__  = interp2app(W_FileIO.descr_init),
    readable = interp2app(W_FileIO.readable_w),
    writable = interp2app(W_FileIO.writable_w),
    )

