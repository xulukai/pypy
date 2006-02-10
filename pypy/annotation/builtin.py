"""
Built-in functions.
"""

import sys
from pypy.annotation.model import SomeInteger, SomeObject, SomeChar, SomeBool
from pypy.annotation.model import SomeString, SomeTuple, SomeSlice
from pypy.annotation.model import SomeUnicodeCodePoint, SomeAddress
from pypy.annotation.model import SomeFloat, unionof
from pypy.annotation.model import SomePBC, SomeInstance, SomeDict
from pypy.annotation.model import SomeExternalObject
from pypy.annotation.model import annotation_to_lltype, lltype_to_annotation, ll_to_annotation
from pypy.annotation.model import add_knowntypedata
from pypy.annotation.model import s_ImpossibleValue
from pypy.annotation.bookkeeper import getbookkeeper
from pypy.annotation import description
from pypy.objspace.flow.model import Constant
import pypy.rpython.rarithmetic
import pypy.rpython.objectmodel
import pypy.rpython.rstack

# convenience only!
def immutablevalue(x):
    return getbookkeeper().immutablevalue(x)

def constpropagate(func, args_s, s_result):
    """Returns s_result unless all args are constants, in which case the
    func() is called and a constant result is returned (it must be contained
    in s_result).
    """
    args = []
    for s in args_s:
        if not s.is_immutable_constant():
            return s_result
        args.append(s.const)
    realresult = func(*args)
    s_realresult = immutablevalue(realresult)
    if not s_result.contains(s_realresult):
        raise Exception("%s%r returned %r, which is not contained in %s" % (
            func, args, realresult, s_result))
    return s_realresult

# ____________________________________________________________

def builtin_range(*args):
    s_step = immutablevalue(1)
    if len(args) == 1:
        s_start = immutablevalue(0)
        s_stop = args[0]
    elif len(args) == 2:
        s_start, s_stop = args
    elif len(args) == 3:
        s_start, s_stop = args[:2]
        s_step = args[2]
    else:
        raise Exception, "range() takes 1 to 3 arguments"
    if not s_step.is_constant():
        step = 0 # this case signals a variable step
    else:
        step = s_step.const
        if step == 0:
            raise Exception, "range() with step zero"
    nonneg = False # so far
    if step > 0:
        nonneg = s_start.nonneg
    elif step < 0:
        nonneg = s_stop.nonneg or (s_stop.is_constant() and s_stop.const >= -1)
    return getbookkeeper().newlist(SomeInteger(nonneg=nonneg), range_step=step)

builtin_xrange = builtin_range # xxx for now allow it

def builtin_bool(s_obj):
    return s_obj.is_true()

def builtin_int(s_obj, s_base=None):
    assert (s_base is None or isinstance(s_base, SomeInteger)
            and s_obj.knowntype == str), "only int(v|string) or int(string,int) expected"
    if s_base is not None:
        args_s = [s_obj, s_base]
    else:
        args_s = [s_obj]
    return constpropagate(int, args_s, SomeInteger())

def restricted_uint(s_obj):    # for r_uint
    return constpropagate(pypy.rpython.rarithmetic.r_uint, [s_obj],
                          SomeInteger(nonneg=True, unsigned=True))

def restricted_longlong(s_obj):    # for r_uint
    return constpropagate(pypy.rpython.rarithmetic.r_longlong, [s_obj],
                          SomeInteger(size=2))

def restricted_ulonglong(s_obj):    # for r_uint
    return constpropagate(pypy.rpython.rarithmetic.r_ulonglong, [s_obj],
                          SomeInteger(size=2, nonneg=True, unsigned=True))

def builtin_float(s_obj):
    return constpropagate(float, [s_obj], SomeFloat())

def builtin_long(s_obj):
    return SomeObject()   # XXX go away

def builtin_chr(s_int):
    return constpropagate(chr, [s_int], SomeChar())

def builtin_unichr(s_int):
    return constpropagate(unichr, [s_int], SomeUnicodeCodePoint())

##def builtin_unicode(s_obj):
##    raise TypeError, "unicode() calls should not happen at interp-level"

def our_issubclass(cls1, cls2):
    """ we're going to try to be less silly in the face of old-style classes"""
    from pypy.annotation.classdef import ClassDef
    if cls2 is object:
        return True
    def classify(cls):
        if isinstance(cls, ClassDef):
            return 'def'
        if cls.__module__ == '__builtin__':
            return 'builtin'
        else:
            return 'cls'
    kind1 = classify(cls1)
    kind2 = classify(cls2)
    if kind1 != 'def' and kind2 != 'def':
        return issubclass(cls1, cls2)
    if kind1 == 'builtin' and kind2 == 'def':
        return False
    elif kind1 == 'def' and kind2 == 'builtin':
        return issubclass(object, cls2)
    else:
        bk = getbookkeeper()
        def toclassdef(kind, cls):
            if kind != 'def':
                return bk.getuniqueclassdef(cls)
            else:
                return cls
        return toclassdef(kind1, cls1).issubclass(toclassdef(kind2, cls2))


def builtin_isinstance(s_obj, s_type, variables=None):
    r = SomeBool() 
    if s_type.is_constant():
        typ = s_type.const
        if issubclass(typ, pypy.rpython.rarithmetic.base_int):
            if s_obj.is_constant():
                r.const = isinstance(s_obj.const, typ)
            else:
                r.const = issubclass(s_obj.knowntype, typ)
        else:
            if typ == long:
                getbookkeeper().warning("isinstance(., long) is not RPython")
                if s_obj.is_constant():
                    r.const = isinstance(s_obj.const, long)
                else:
                    if type(s_obj) is not SomeObject: # only SomeObjects could be longs
                        # type(s_obj) < SomeObject -> SomeBool(False)
                        # type(s_obj) == SomeObject -> SomeBool()
                        r.const = False
                return r
                
            assert not issubclass(typ, (int,long)) or typ in (bool, int), (
                "for integers only isinstance(.,int|r_uint) are supported")
 
            if s_obj.is_constant():
                r.const = isinstance(s_obj.const, typ)
            elif our_issubclass(s_obj.knowntype, typ):
                if not s_obj.can_be_none():
                    r.const = True 
            elif not our_issubclass(typ, s_obj.knowntype): 
                r.const = False
            elif s_obj.knowntype == int and typ == bool: # xxx this will explode in case of generalisation
                                                   # from bool to int, notice that isinstance( , bool|int)
                                                   # is quite border case for RPython
                r.const = False
        # XXX HACK HACK HACK
        # XXX HACK HACK HACK
        # XXX HACK HACK HACK
        bk = getbookkeeper()
        if variables is None:
            fn, block, i = bk.position_key
            op = block.operations[i]
            assert op.opname == "simple_call" 
            assert len(op.args) == 3
            assert op.args[0] == Constant(isinstance)
            variables = [op.args[1]]
        for variable in variables:
            assert bk.annotator.binding(variable) == s_obj
        r.knowntypedata = {}
        add_knowntypedata(r.knowntypedata, True, variables, bk.valueoftype(typ))
    return r

def builtin_hasattr(s_obj, s_attr):
    if not s_attr.is_constant() or not isinstance(s_attr.const, str):
        getbookkeeper().warning('hasattr(%r, %r) is not RPythonic enough' %
                                (s_obj, s_attr))
    r = SomeBool()
    if s_obj.is_immutable_constant():
        r.const = hasattr(s_obj.const, s_attr.const)
    elif (isinstance(s_obj, SomePBC)
          and s_obj.getKind() is description.FrozenDesc):
       answers = {}    
       for d in s_obj.descriptions:
           answer = (d.s_read_attribute(s_attr.const) != s_ImpossibleValue)
           answers[answer] = True
       if len(answers) == 1:
           r.const, = answers
    return r

##def builtin_callable(s_obj):
##    return SomeBool()

def builtin_tuple(s_iterable):
    if isinstance(s_iterable, SomeTuple):
        return s_iterable
    return SomeObject()

def builtin_list(s_iterable):
    s_iter = s_iterable.iter()
    return getbookkeeper().newlist(s_iter.next())

def builtin_zip(s_iterable1, s_iterable2):
    s_iter1 = s_iterable1.iter()
    s_iter2 = s_iterable2.iter()
    s_tup = SomeTuple((s_iter1.next(),s_iter2.next()))
    return getbookkeeper().newlist(s_tup)

def builtin_min(*s_values):
    if len(s_values) == 1: # xxx do we support this?
        s_iter = s_values[0].iter()
        return s_iter.next()
    else:
        return unionof(*s_values)

builtin_max = builtin_min

def builtin_apply(*stuff):
    getbookkeeper().warning("ignoring apply%r" % (stuff,))
    return SomeObject()

def builtin_slice(*args):
    bk = getbookkeeper()
    if len(args) == 1:
        return SomeSlice(
            bk.immutablevalue(None), args[0], bk.immutablevalue(None))
    elif len(args) == 2:
        return SomeSlice(
            args[0], args[1], bk.immutablevalue(None))
    elif len(args) == 3:
        return SomeSlice(
            args[0], args[1], args[2])
    else:
        raise Exception, "bogus call to slice()"
        

def exception_init(s_self, *args):
    pass   # XXX check correctness of args, maybe

def object_init(s_self, *args):
    # ignore - mostly used for abstract classes initialization
    pass


def count(s_obj):
    return SomeInteger()

def conf():
    return SomeString()

def rarith_intmask(s_obj):
    return SomeInteger()

def robjmodel_instantiate(s_clspbc):
    assert isinstance(s_clspbc, SomePBC)
    clsdef = None
    more_than_one = len(s_clspbc.descriptions)
    for desc in s_clspbc.descriptions:
        cdef = desc.getuniqueclassdef()
        if more_than_one:
            getbookkeeper().needs_generic_instantiate[cdef] = True
        if not clsdef:
            clsdef = cdef
        else:
            clsdef = clsdef.commonbase(cdef)
    return SomeInstance(clsdef)

def robjmodel_we_are_translated():
    return immutablevalue(True)

def robjmodel_r_dict(s_eqfn, s_hashfn):
    dictdef = getbookkeeper().getdictdef()
    dictdef.dictkey.update_rdict_annotations(s_eqfn, s_hashfn)
    return SomeDict(dictdef)


def robjmodel_hlinvoke(s_repr, s_llcallable, *args_s):
    from pypy.rpython import rmodel
    assert s_repr.is_constant() and isinstance(s_repr.const, rmodel.Repr),"hlinvoke expects a constant repr as first argument"
    r_func, nimplicitarg  = s_repr.const.get_r_implfunc()

    nbargs = len(args_s) + nimplicitarg 
    s_sigs = r_func.get_s_signatures((nbargs, (), False, False))
    if len(s_sigs) != 1:
        raise TyperError("cannot hlinvoke callable %r with not uniform"
                         "annotations: %r" % (s_repr.const,
                                              s_sigs))
    _, s_ret = s_sigs[0]
    rresult = r_func.rtyper.getrepr(s_ret)

    return lltype_to_annotation(rresult.lowleveltype)

def robjmodel_keepalive_until_here(*args_s):
    return immutablevalue(None)

def robjmodel_hint(s, **kwds_s):
    return s

def robjmodel_cast_ptr_to_adr(s):
    return SomeAddress()

def robjmodel_cast_adr_to_ptr(s, s_type):
    assert s_type.is_constant()
    return SomePtr(s_type.const)

def rstack_yield_current_frame_to_caller():
    return SomeExternalObject(pypy.rpython.rstack.frame_stack_top)
    

##def rarith_ovfcheck(s_obj):
##    if isinstance(s_obj, SomeInteger) and s_obj.unsigned:
##        getbookkeeper().warning("ovfcheck on unsigned")
##    return s_obj

##def rarith_ovfcheck_lshift(s_obj1, s_obj2):
##    if isinstance(s_obj1, SomeInteger) and s_obj1.unsigned:
##        getbookkeeper().warning("ovfcheck_lshift with unsigned")
##    return SomeInteger()

def unicodedata_decimal(s_uchr):
    raise TypeError, "unicodedate.decimal() calls should not happen at interp-level"    

def test(*args):
    return SomeBool()

def import_func(*args):
    return SomeObject()

# collect all functions
import __builtin__
BUILTIN_ANALYZERS = {}
EXTERNAL_TYPE_ANALYZERS = {}
for name, value in globals().items():
    if name.startswith('builtin_'):
        original = getattr(__builtin__, name[8:])
        BUILTIN_ANALYZERS[original] = value

BUILTIN_ANALYZERS[pypy.rpython.rarithmetic.r_uint] = restricted_uint
BUILTIN_ANALYZERS[pypy.rpython.rarithmetic.r_longlong] = restricted_longlong
BUILTIN_ANALYZERS[pypy.rpython.rarithmetic.r_ulonglong] = restricted_ulonglong
##BUILTIN_ANALYZERS[pypy.rpython.rarithmetic.ovfcheck] = rarith_ovfcheck
##BUILTIN_ANALYZERS[pypy.rpython.rarithmetic.ovfcheck_lshift] = rarith_ovfcheck_lshift
BUILTIN_ANALYZERS[pypy.rpython.rarithmetic.intmask] = rarith_intmask
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.instantiate] = robjmodel_instantiate
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.we_are_translated] = (
    robjmodel_we_are_translated)
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.r_dict] = robjmodel_r_dict
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.hlinvoke] = robjmodel_hlinvoke
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.keepalive_until_here] = robjmodel_keepalive_until_here
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.hint] = robjmodel_hint
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.cast_ptr_to_adr] = robjmodel_cast_ptr_to_adr
BUILTIN_ANALYZERS[pypy.rpython.objectmodel.cast_adr_to_ptr] = robjmodel_cast_adr_to_ptr
BUILTIN_ANALYZERS[pypy.rpython.rstack.yield_current_frame_to_caller] = (
    rstack_yield_current_frame_to_caller)

BUILTIN_ANALYZERS[Exception.__init__.im_func] = exception_init
BUILTIN_ANALYZERS[OSError.__init__.im_func] = exception_init
# this one is needed otherwise when annotating assert in a test we may try to annotate 
# py.test AssertionError.__init__ .
BUILTIN_ANALYZERS[AssertionError.__init__.im_func] = exception_init
BUILTIN_ANALYZERS[sys.getdefaultencoding] = conf
import unicodedata
BUILTIN_ANALYZERS[unicodedata.decimal] = unicodedata_decimal # xxx

# object - just ignore object.__init__
BUILTIN_ANALYZERS[object.__init__] = object_init

# import
BUILTIN_ANALYZERS[__import__] = import_func

# annotation of low-level types
from pypy.annotation.model import SomePtr
from pypy.rpython.lltypesystem import lltype

def malloc(T, n=None):
    assert n is None or n.knowntype == int
    assert T.is_constant()
    if n is not None:
        n = 1
    p = lltype.malloc(T.const, n)
    r = SomePtr(lltype.typeOf(p))
    return r

def typeOf(s_val):
    lltype = annotation_to_lltype(s_val, info="in typeOf(): ")
    return immutablevalue(lltype)

def cast_primitive(T, s_v):
    assert T.is_constant()
    return ll_to_annotation(lltype.cast_primitive(T.const, annotation_to_lltype(s_v)._defl()))

def nullptr(T):
    assert T.is_constant()
    p = lltype.nullptr(T.const)
    return immutablevalue(p)

def cast_pointer(PtrT, s_p):
    assert isinstance(s_p, SomePtr), "casting of non-pointer: %r" % s_p
    assert PtrT.is_constant()
    cast_p = lltype.cast_pointer(PtrT.const, s_p.ll_ptrtype._defl())
    return SomePtr(ll_ptrtype=lltype.typeOf(cast_p))

def cast_ptr_to_int(s_ptr): # xxx
    return SomeInteger()

def getRuntimeTypeInfo(T):
    assert T.is_constant()
    return immutablevalue(lltype.getRuntimeTypeInfo(T.const))

def runtime_type_info(s_p):
    assert isinstance(s_p, SomePtr), "runtime_type_info of non-pointer: %r" % s_p
    return SomePtr(lltype.typeOf(lltype.runtime_type_info(s_p.ll_ptrtype._example())))

BUILTIN_ANALYZERS[lltype.malloc] = malloc
BUILTIN_ANALYZERS[lltype.typeOf] = typeOf
BUILTIN_ANALYZERS[lltype.cast_primitive] = cast_primitive
BUILTIN_ANALYZERS[lltype.nullptr] = nullptr
BUILTIN_ANALYZERS[lltype.cast_pointer] = cast_pointer
BUILTIN_ANALYZERS[lltype.cast_ptr_to_int] = cast_ptr_to_int
BUILTIN_ANALYZERS[lltype.getRuntimeTypeInfo] = getRuntimeTypeInfo
BUILTIN_ANALYZERS[lltype.runtime_type_info] = runtime_type_info

# ootype
from pypy.annotation.model import SomeOOInstance, SomeOOClass
from pypy.rpython.ootypesystem import ootype

def new(I):
    assert I.is_constant()
    i = ootype.new(I.const)
    r = SomeOOInstance(ootype.typeOf(i))
    return r

def null(I_OR_SM):
    assert I_OR_SM.is_constant()
    null = ootype.null(I_OR_SM.const)
    r = lltype_to_annotation(ootype.typeOf(null))
    return r

def instanceof(i, I):
    assert I.is_constant()
    assert isinstance(I.const, ootype.Instance)
    return SomeBool()

def classof(i):
    assert isinstance(i, SomeOOInstance) 
    return SomeOOClass(i.ootype)

def runtimenew(c):
    assert isinstance(c, SomeOOClass)
    if c.ootype is None:
        return SomeImpossibleValue()   # can't call runtimenew(NULL)
    else:
        return SomeOOInstance(c.ootype)

def ooidentityhash(i):
    assert isinstance(i, SomeOOInstance)
    return SomeInteger()

BUILTIN_ANALYZERS[ootype.instanceof] = instanceof
BUILTIN_ANALYZERS[ootype.new] = new
BUILTIN_ANALYZERS[ootype.null] = null
BUILTIN_ANALYZERS[ootype.runtimenew] = runtimenew
BUILTIN_ANALYZERS[ootype.classof] = classof
BUILTIN_ANALYZERS[ootype.ooidentityhash] = ooidentityhash

#________________________________
# non-gc objects

def robjmodel_free_non_gc_object(obj):
    pass

BUILTIN_ANALYZERS[pypy.rpython.objectmodel.free_non_gc_object] = (
    robjmodel_free_non_gc_object)

#_________________________________
# memory address

from pypy.rpython.memory import lladdress

def raw_malloc(s_size):
    assert isinstance(s_size, SomeInteger) #XXX add noneg...?
    return SomeAddress()

def raw_free(s_addr):
    assert isinstance(s_addr, SomeAddress)
    assert not s_addr.is_null

def raw_memcopy(s_addr1, s_addr2, s_int):
    assert isinstance(s_addr1, SomeAddress)
    assert not s_addr1.is_null
    assert isinstance(s_addr2, SomeAddress)
    assert not s_addr2.is_null
    assert isinstance(s_int, SomeInteger) #XXX add noneg...?

BUILTIN_ANALYZERS[lladdress.raw_malloc] = raw_malloc
BUILTIN_ANALYZERS[lladdress.raw_free] = raw_free
BUILTIN_ANALYZERS[lladdress.raw_memcopy] = raw_memcopy

#_________________________________
# offsetof/sizeof

from pypy.rpython.lltypesystem import llmemory 

def offsetof(TYPE, fldname):
    return SomeInteger()

BUILTIN_ANALYZERS[llmemory.offsetof] = offsetof

from pypy.rpython.l3interp import l3interp
def l3malloc(size):
    return SomeAddress()

BUILTIN_ANALYZERS[l3interp.malloc] = l3malloc

#_________________________________
# external functions


from pypy.rpython import extfunctable

def update_exttables():

    # import annotation information for external functions 
    # from the extfunctable.table  into our own annotation specific table 
    for func, extfuncinfo in extfunctable.table.iteritems():
        BUILTIN_ANALYZERS[func] = extfuncinfo.annotation 

    # import annotation information for external types
    # from the extfunctable.typetable  into our own annotation specific table 
    for typ, exttypeinfo in extfunctable.typetable.iteritems():
        EXTERNAL_TYPE_ANALYZERS[typ] = exttypeinfo.get_annotations()

# Note: calls to declare() may occur after builtin.py is first imported.
# We must track future changes to the extfunctables.
extfunctable.table_callbacks.append(update_exttables)
update_exttables()
