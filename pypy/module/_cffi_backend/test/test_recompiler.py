import os, py

from rpython.tool.udir import udir
from pypy.interpreter.gateway import unwrap_spec, interp2app
from pypy.module._cffi_backend.newtype import _clean_cache
import pypy.module.cpyext.api     # side-effect of pre-importing it


@unwrap_spec(cdef=str, module_name=str, source=str)
def prepare(space, cdef, module_name, source):
    try:
        from cffi import FFI              # <== the system one, which
        from _cffi1 import recompiler     # needs to be at least cffi 1.0.0b3
    except ImportError:
        py.test.skip("system cffi module not found or older than 1.0.0")
    space.appexec([], """():
        import _cffi_backend     # force it to be initialized
    """)
    module_name = '_CFFI_' + module_name
    rdir = udir.ensure('recompiler', dir=1)
    rdir.join('Python.h').write(
        '#define PYPY_VERSION XX\n'
        '#define PyMODINIT_FUNC /*exported*/\n'
        )
    c_file  = str(rdir.join('%s.c'  % module_name))
    so_file = str(rdir.join('%s.so' % module_name))
    ffi = FFI()
    ffi.cdef(cdef)
    ffi.set_source(module_name, source)
    ffi.emit_c_code(c_file)
    err = os.system("cd '%s' && gcc -shared -fPIC -g -I. '%s' -o '%s'" % (
        str(rdir),
        os.path.basename(c_file),
        os.path.basename(so_file)))
    if err != 0:
        raise Exception("gcc error")

    args_w = [space.wrap(module_name), space.wrap(so_file)]
    return space.appexec(args_w, """(modulename, filename):
        import imp
        mod = imp.load_dynamic(modulename, filename)
        return (mod.ffi, mod.lib)
    """)


class AppTestRecompiler:
    spaceconfig = dict(usemodules=['_cffi_backend', 'imp'])

    def setup_class(cls):
        cls.w_prepare = cls.space.wrap(interp2app(prepare))

    def setup_method(self, meth):
        self._w_modules = self.space.appexec([], """():
            import sys
            return set(sys.modules)
        """)

    def teardown_method(self, meth):
        self.space.appexec([self._w_modules], """(old_modules):
            import sys
            for key in sys.modules.keys():
                if key not in old_modules:
                    del sys.modules[key]
        """)
        _clean_cache(self.space)

    def test_math_sin(self):
        import math
        ffi, lib = self.prepare(
            "float sin(double); double cos(double);",
            'test_math_sin',
            '#include <math.h>')
        assert lib.cos(1.43) == math.cos(1.43)

    def test_funcarg_ptr(self):
        ffi, lib = self.prepare(
            "int foo(int *);",
            'test_funcarg_ptr',
            'int foo(int *p) { return *p; }')
        assert lib.foo([-12345]) == -12345

    def test_funcres_ptr(self):
        ffi, lib = self.prepare(
            "int *foo(void);",
            'test_funcres_ptr',
            'int *foo(void) { static int x=-12345; return &x; }')
        assert lib.foo()[0] == -12345

    def test_global_var_array(self):
        ffi, lib = self.prepare(
            "int a[100];",
            'test_global_var_array',
            'int a[100] = { 9999 };')
        lib.a[42] = 123456
        assert lib.a[42] == 123456
        assert lib.a[0] == 9999

    def test_verify_typedef(self):
        ffi, lib = self.prepare(
            "typedef int **foo_t;",
            'test_verify_typedef',
            'typedef int **foo_t;')
        assert ffi.sizeof("foo_t") == ffi.sizeof("void *")

    def test_verify_typedef_dotdotdot(self):
        ffi, lib = self.prepare(
            "typedef ... foo_t;",
            'test_verify_typedef_dotdotdot',
            'typedef int **foo_t;')
        # did not crash

    def test_verify_typedef_star_dotdotdot(self):
        ffi, lib = self.prepare(
            "typedef ... *foo_t;",
            'test_verify_typedef_star_dotdotdot',
            'typedef int **foo_t;')
        # did not crash

    def test_global_var_int(self):
        ffi, lib = self.prepare(
            "int a, b, c;",
            'test_global_var_int',
            'int a = 999, b, c;')
        assert lib.a == 999
        lib.a -= 1001
        assert lib.a == -2
        lib.a = -2147483648
        assert lib.a == -2147483648
        raises(OverflowError, "lib.a = 2147483648")
        raises(OverflowError, "lib.a = -2147483649")
        lib.b = 525      # try with the first access being in setattr, too
        assert lib.b == 525
        raises(AttributeError, "del lib.a")
        raises(AttributeError, "del lib.c")
        raises(AttributeError, "del lib.foobarbaz")

    def test_macro(self):
        ffi, lib = self.prepare(
            "#define FOOBAR ...",
            'test_macro',
            "#define FOOBAR (-6912)")
        assert lib.FOOBAR == -6912
        raises(AttributeError, "lib.FOOBAR = 2")

    def test_macro_check_value(self):
        # the value '-0x80000000' in C sources does not have a clear meaning
        # to me; it appears to have a different effect than '-2147483648'...
        # Moreover, on 32-bits, -2147483648 is actually equal to
        # -2147483648U, which in turn is equal to 2147483648U and so positive.
        import sys
        vals = ['42', '-42', '0x80000000', '-2147483648',
                '0', '9223372036854775809ULL',
                '-9223372036854775807LL']
        if sys.maxsize <= 2**32:
            vals.remove('-2147483648')

        cdef_lines = ['#define FOO_%d_%d %s' % (i, j, vals[i])
                      for i in range(len(vals))
                      for j in range(len(vals))]

        verify_lines = ['#define FOO_%d_%d %s' % (i, j, vals[j])  # [j], not [i]
                        for i in range(len(vals))
                        for j in range(len(vals))]

        ffi, lib = self.prepare(
            '\n'.join(cdef_lines),
            'test_macro_check_value_ok',
            '\n'.join(verify_lines))

        for j in range(len(vals)):
            c_got = int(vals[j].replace('U', '').replace('L', ''), 0)
            c_compiler_msg = str(c_got)
            if c_got > 0:
                c_compiler_msg += ' (0x%x)' % (c_got,)
            #
            for i in range(len(vals)):
                attrname = 'FOO_%d_%d' % (i, j)
                if i == j:
                    x = getattr(lib, attrname)
                    assert x == c_got
                else:
                    e = raises(ffi.error, getattr, lib, attrname)
                    assert str(e.value) == (
                        "the C compiler says '%s' is equal to "
                        "%s, but the cdef disagrees" % (attrname, c_compiler_msg))

    def test_constant(self):
        ffi, lib = self.prepare(
            "static const int FOOBAR;",
            'test_constant',
            "#define FOOBAR (-6912)")
        assert lib.FOOBAR == -6912
        raises(AttributeError, "lib.FOOBAR = 2")

    def test_constant_nonint(self):
        ffi, lib = self.prepare(
            "static const double FOOBAR;",
            'test_constant_nonint',
            "#define FOOBAR (-6912.5)")
        assert lib.FOOBAR == -6912.5
        raises(AttributeError, "lib.FOOBAR = 2")

    def test_constant_ptr(self):
        ffi, lib = self.prepare(
            "static double *const FOOBAR;",
            'test_constant_ptr',
            "#define FOOBAR NULL")
        assert lib.FOOBAR == ffi.NULL
        assert ffi.typeof(lib.FOOBAR) == ffi.typeof("double *")

    def test_dir():
        ffi = FFI()
        ffi.cdef("int ff(int); int aa; static const int my_constant;")
        lib = verify(ffi, 'test_dir', """
            #define my_constant  (-45)
            int aa;
            int ff(int x) { return x+aa; }
        """)
        lib.aa = 5
        assert dir(lib) == ['aa', 'ff', 'my_constant']

    def test_verify_opaque_struct():
        ffi = FFI()
        ffi.cdef("struct foo_s;")
        lib = verify(ffi, 'test_verify_opaque_struct', "struct foo_s;")
        assert ffi.typeof("struct foo_s").cname == "struct foo_s"

    def test_verify_opaque_union():
        ffi = FFI()
        ffi.cdef("union foo_s;")
        lib = verify(ffi, 'test_verify_opaque_union', "union foo_s;")
        assert ffi.typeof("union foo_s").cname == "union foo_s"

    def test_verify_struct():
        ffi = FFI()
        ffi.cdef("""struct foo_s { int b; short a; ...; };
                    struct bar_s { struct foo_s *f; };""")
        lib = verify(ffi, 'test_verify_struct',
                     """struct foo_s { short a; int b; };
                        struct bar_s { struct foo_s *f; };""")
        ffi.typeof("struct bar_s *")
        p = ffi.new("struct foo_s *", {'a': -32768, 'b': -2147483648})
        assert p.a == -32768
        assert p.b == -2147483648
        raises(OverflowError, "p.a -= 1")
        raises(OverflowError, "p.b -= 1")
        q = ffi.new("struct bar_s *", {'f': p})
        assert q.f == p
        #
        assert ffi.offsetof("struct foo_s", "a") == 0
        assert ffi.offsetof("struct foo_s", "b") == 4
        #
        raises(TypeError, ffi.addressof, p)
        assert ffi.addressof(p[0]) == p
        assert ffi.typeof(ffi.addressof(p[0])) is ffi.typeof("struct foo_s *")
        assert ffi.typeof(ffi.addressof(p, "b")) is ffi.typeof("int *")
        assert ffi.addressof(p, "b")[0] == p.b

    def test_verify_exact_field_offset():
        ffi = FFI()
        ffi.cdef("""struct foo_s { int b; short a; };""")
        lib = verify(ffi, 'test_verify_exact_field_offset',
                     """struct foo_s { short a; int b; };""")
        e = raises(ffi.error, ffi.new, "struct foo_s *", [])    # lazily
        assert str(e.value) == ("struct foo_s: wrong offset for field 'b' (cdef "
                           'says 0, but C compiler says 4). fix it or use "...;" '
                           "in the cdef for struct foo_s to make it flexible")

    def test_type_caching():
        ffi1 = FFI(); ffi1.cdef("struct foo_s;")
        ffi2 = FFI(); ffi2.cdef("struct foo_s;")    # different one!
        lib1 = verify(ffi1, 'test_type_caching_1', 'struct foo_s;')
        lib2 = verify(ffi2, 'test_type_caching_2', 'struct foo_s;')
        # shared types
        assert ffi1.typeof("long") is ffi2.typeof("long")
        assert ffi1.typeof("long**") is ffi2.typeof("long * *")
        assert ffi1.typeof("long(*)(int, ...)") is ffi2.typeof("long(*)(int, ...)")
        # non-shared types
        assert ffi1.typeof("struct foo_s") is not ffi2.typeof("struct foo_s")
        assert ffi1.typeof("struct foo_s *") is not ffi2.typeof("struct foo_s *")
        assert ffi1.typeof("struct foo_s*(*)()") is not (
            ffi2.typeof("struct foo_s*(*)()"))
        assert ffi1.typeof("void(*)(struct foo_s*)") is not (
            ffi2.typeof("void(*)(struct foo_s*)"))

    def test_verify_enum():
        ffi = FFI()
        ffi.cdef("""enum e1 { B1, A1, ... }; enum e2 { B2, A2, ... };""")
        lib = verify(ffi, 'test_verify_enum',
                     "enum e1 { A1, B1, C1=%d };" % sys.maxsize +
                     "enum e2 { A2, B2, C2 };")
        ffi.typeof("enum e1")
        ffi.typeof("enum e2")
        assert lib.A1 == 0
        assert lib.B1 == 1
        assert lib.A2 == 0
        assert lib.B2 == 1
        assert ffi.sizeof("enum e1") == ffi.sizeof("long")
        assert ffi.sizeof("enum e2") == ffi.sizeof("int")
        assert repr(ffi.cast("enum e1", 0)) == "<cdata 'enum e1' 0: A1>"

    def test_duplicate_enum():
        ffi = FFI()
        ffi.cdef("enum e1 { A1, ... }; enum e2 { A1, ... };")
        raises(VerificationError, verify, ffi, 'test_duplicate_enum',
                        "enum e1 { A1 }; enum e2 { B1 };")

    def test_dotdotdot_length_of_array_field():
        ffi = FFI()
        ffi.cdef("struct foo_s { int a[...]; int b[...]; };")
        verify(ffi, 'test_dotdotdot_length_of_array_field',
               "struct foo_s { int a[42]; int b[11]; };")
        assert ffi.sizeof("struct foo_s") == (42 + 11) * 4
        p = ffi.new("struct foo_s *")
        assert p.a[41] == p.b[10] == 0
        raises(IndexError, "p.a[42]")
        raises(IndexError, "p.b[11]")

    def test_dotdotdot_global_array():
        ffi = FFI()
        ffi.cdef("int aa[...]; int bb[...];")
        lib = verify(ffi, 'test_dotdotdot_global_array',
                     "int aa[41]; int bb[12];")
        assert ffi.sizeof(lib.aa) == 41 * 4
        assert ffi.sizeof(lib.bb) == 12 * 4
        assert lib.aa[40] == lib.bb[11] == 0
        raises(IndexError, "lib.aa[41]")
        raises(IndexError, "lib.bb[12]")

    def test_misdeclared_field_1():
        ffi = FFI()
        ffi.cdef("struct foo_s { int a[5]; };")
        verify(ffi, 'test_misdeclared_field_1',
               "struct foo_s { int a[6]; };")
        assert ffi.sizeof("struct foo_s") == 24  # found by the actual C code
        p = ffi.new("struct foo_s *")
        # lazily build the fields and boom:
        e = raises(ffi.error, "p.a")
        assert str(e.value).startswith("struct foo_s: wrong size for field 'a' "
                                       "(cdef says 20, but C compiler says 24)")

    def test_open_array_in_struct():
        ffi = FFI()
        ffi.cdef("struct foo_s { int b; int a[]; };")
        verify(ffi, 'test_open_array_in_struct',
               "struct foo_s { int b; int a[]; };")
        assert ffi.sizeof("struct foo_s") == 4
        p = ffi.new("struct foo_s *", [5, [10, 20, 30]])
        assert p.a[2] == 30

    def test_math_sin_type():
        ffi = FFI()
        ffi.cdef("double sin(double);")
        lib = verify(ffi, 'test_math_sin_type', '#include <math.h>')
        # 'lib.sin' is typed as a <built-in method> object on lib
        assert ffi.typeof(lib.sin).cname == "double(*)(double)"
        # 'x' is another <built-in method> object on lib, made very indirectly
        x = type(lib).__dir__.__get__(lib)
        raises(TypeError, ffi.typeof, x)

    def test_verify_anonymous_struct_with_typedef():
        ffi = FFI()
        ffi.cdef("typedef struct { int a; long b; ...; } foo_t;")
        verify(ffi, 'test_verify_anonymous_struct_with_typedef',
               "typedef struct { long b; int hidden, a; } foo_t;")
        p = ffi.new("foo_t *", {'b': 42})
        assert p.b == 42
        assert repr(p).startswith("<cdata 'foo_t *' ")

    def test_verify_anonymous_struct_with_star_typedef():
        ffi = FFI()
        ffi.cdef("typedef struct { int a; long b; } *foo_t;")
        verify(ffi, 'test_verify_anonymous_struct_with_star_typedef',
               "typedef struct { int a; long b; } *foo_t;")
        p = ffi.new("foo_t", {'b': 42})
        assert p.b == 42

    def test_verify_anonymous_enum_with_typedef():
        ffi = FFI()
        ffi.cdef("typedef enum { AA, ... } e1;")
        lib = verify(ffi, 'test_verify_anonymous_enum_with_typedef1',
                     "typedef enum { BB, CC, AA } e1;")
        assert lib.AA == 2
        assert ffi.sizeof("e1") == ffi.sizeof("int")
        assert repr(ffi.cast("e1", 2)) == "<cdata 'e1' 2: AA>"
        #
        ffi = FFI()
        ffi.cdef("typedef enum { AA=%d } e1;" % sys.maxsize)
        lib = verify(ffi, 'test_verify_anonymous_enum_with_typedef2',
                     "typedef enum { AA=%d } e1;" % sys.maxsize)
        assert lib.AA == sys.maxsize
        assert ffi.sizeof("e1") == ffi.sizeof("long")

    def test_unique_types():
        CDEF = "struct foo_s; union foo_u; enum foo_e { AA };"
        ffi1 = FFI(); ffi1.cdef(CDEF); verify(ffi1, "test_unique_types_1", CDEF)
        ffi2 = FFI(); ffi2.cdef(CDEF); verify(ffi2, "test_unique_types_2", CDEF)
        #
        assert ffi1.typeof("char") is ffi2.typeof("char ")
        assert ffi1.typeof("long") is ffi2.typeof("signed long int")
        assert ffi1.typeof("double *") is ffi2.typeof("double*")
        assert ffi1.typeof("int ***") is ffi2.typeof(" int * * *")
        assert ffi1.typeof("int[]") is ffi2.typeof("signed int[]")
        assert ffi1.typeof("signed int*[17]") is ffi2.typeof("int *[17]")
        assert ffi1.typeof("void") is ffi2.typeof("void")
        assert ffi1.typeof("int(*)(int,int)") is ffi2.typeof("int(*)(int,int)")
        #
        # these depend on user-defined data, so should not be shared
        for name in ["struct foo_s",
                     "union foo_u *",
                     "enum foo_e",
                     "struct foo_s *(*)()",
                     "void(*)(struct foo_s *)",
                     "struct foo_s *(*[5])[8]",
                     ]:
            assert ffi1.typeof(name) is not ffi2.typeof(name)
        # sanity check: twice 'ffi1'
        assert ffi1.typeof("struct foo_s*") is ffi1.typeof("struct foo_s *")

    def test_module_name_in_package():
        ffi = FFI()
        ffi.cdef("int foo(int);")
        recompiler.recompile(ffi, "test_module_name_in_package.mymod",
                             "int foo(int x) { return x + 32; }",
                             tmpdir=str(udir))
        old_sys_path = sys.path[:]
        try:
            package_dir = udir.join('test_module_name_in_package')
            assert os.path.isdir(str(package_dir))
            assert len(os.listdir(str(package_dir))) > 0
            package_dir.join('__init__.py').write('')
            #
            sys.path.insert(0, str(udir))
            import test_module_name_in_package.mymod
            assert test_module_name_in_package.mymod.lib.foo(10) == 42
        finally:
            sys.path[:] = old_sys_path

    def test_bad_size_of_global_1():
        ffi = FFI()
        ffi.cdef("short glob;")
        lib = verify(ffi, "test_bad_size_of_global_1", "long glob;")
        raises(ffi.error, "lib.glob")

    def test_bad_size_of_global_2():
        ffi = FFI()
        ffi.cdef("int glob[10];")
        lib = verify(ffi, "test_bad_size_of_global_2", "int glob[9];")
        e = raises(ffi.error, "lib.glob")
        assert str(e.value) == ("global variable 'glob' should be 40 bytes "
                                "according to the cdef, but is actually 36")

    def test_unspecified_size_of_global():
        ffi = FFI()
        ffi.cdef("int glob[];")
        lib = verify(ffi, "test_unspecified_size_of_global", "int glob[10];")
        lib.glob    # does not crash

    def test_include_1():
        ffi1 = FFI()
        ffi1.cdef("typedef double foo_t;")
        verify(ffi1, "test_include_1_parent", "typedef double foo_t;")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("foo_t ff1(foo_t);")
        lib = verify(ffi, "test_include_1", "double ff1(double x) { return 42.5; }")
        assert lib.ff1(0) == 42.5

    def test_include_1b():
        ffi1 = FFI()
        ffi1.cdef("int foo1(int);")
        verify(ffi1, "test_include_1b_parent", "int foo1(int x) { return x + 10; }")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("int foo2(int);")
        lib = verify(ffi, "test_include_1b", "int foo2(int x) { return x - 5; }")
        assert lib.foo2(42) == 37
        assert lib.foo1(42) == 52

    def test_include_2():
        ffi1 = FFI()
        ffi1.cdef("struct foo_s { int x, y; };")
        verify(ffi1, "test_include_2_parent", "struct foo_s { int x, y; };")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("struct foo_s *ff2(struct foo_s *);")
        lib = verify(ffi, "test_include_2",
                     "struct foo_s { int x, y; }; //usually from a #include\n"
                     "struct foo_s *ff2(struct foo_s *p) { p->y++; return p; }")
        p = ffi.new("struct foo_s *")
        p.y = 41
        q = lib.ff2(p)
        assert q == p
        assert p.y == 42

    def test_include_3():
        ffi1 = FFI()
        ffi1.cdef("typedef short sshort_t;")
        verify(ffi1, "test_include_3_parent", "typedef short sshort_t;")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("sshort_t ff3(sshort_t);")
        lib = verify(ffi, "test_include_3",
                     "typedef short sshort_t; //usually from a #include\n"
                     "sshort_t ff3(sshort_t x) { return x + 42; }")
        assert lib.ff3(10) == 52
        assert ffi.typeof(ffi.cast("sshort_t", 42)) is ffi.typeof("short")

    def test_include_4():
        ffi1 = FFI()
        ffi1.cdef("typedef struct { int x; } mystruct_t;")
        verify(ffi1, "test_include_4_parent",
               "typedef struct { int x; } mystruct_t;")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("mystruct_t *ff4(mystruct_t *);")
        lib = verify(ffi, "test_include_4",
               "typedef struct {int x; } mystruct_t; //usually from a #include\n"
               "mystruct_t *ff4(mystruct_t *p) { p->x += 42; return p; }")
        p = ffi.new("mystruct_t *", [10])
        q = lib.ff4(p)
        assert q == p
        assert p.x == 52

    def test_include_5():
        py.test.xfail("also fails in 0.9.3")
        ffi1 = FFI()
        ffi1.cdef("typedef struct { int x; } *mystruct_p;")
        verify(ffi1, "test_include_5_parent",
               "typedef struct { int x; } *mystruct_p;")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("mystruct_p ff5(mystruct_p);")
        lib = verify(ffi, "test_include_5",
               "typedef struct {int x; } *mystruct_p; //usually from a #include\n"
               "mystruct_p ff5(mystruct_p p) { p->x += 42; return p; }")
        p = ffi.new("mystruct_p", [10])
        q = lib.ff5(p)
        assert q == p
        assert p.x == 52

    def test_include_6():
        ffi1 = FFI()
        ffi1.cdef("typedef ... mystruct_t;")
        verify(ffi1, "test_include_6_parent",
               "typedef struct _mystruct_s mystruct_t;")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("mystruct_t *ff6(void); int ff6b(mystruct_t *);")
        lib = verify(ffi, "test_include_6",
               "typedef struct _mystruct_s mystruct_t; //usually from a #include\n"
               "struct _mystruct_s { int x; };\n"
               "static mystruct_t result_struct = { 42 };\n"
               "mystruct_t *ff6(void) { return &result_struct; }\n"
               "int ff6b(mystruct_t *p) { return p->x; }")
        p = lib.ff6()
        assert ffi.cast("int *", p)[0] == 42
        assert lib.ff6b(p) == 42

    def test_include_7():
        ffi1 = FFI()
        ffi1.cdef("typedef ... mystruct_t;\n"
                  "int ff7b(mystruct_t *);")
        verify(ffi1, "test_include_7_parent",
               "typedef struct { int x; } mystruct_t;\n"
               "int ff7b(mystruct_t *p) { return p->x; }")
        ffi = FFI()
        ffi.include(ffi1)
        ffi.cdef("mystruct_t *ff7(void);")
        lib = verify(ffi, "test_include_7",
               "typedef struct { int x; } mystruct_t; //usually from a #include\n"
               "static mystruct_t result_struct = { 42 };"
               "mystruct_t *ff7(void) { return &result_struct; }")
        p = lib.ff7()
        assert ffi.cast("int *", p)[0] == 42
        assert lib.ff7b(p) == 42
