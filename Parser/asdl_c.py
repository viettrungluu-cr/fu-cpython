#! /usr/bin/env python
"""Generate C code from an ASDL description."""

# TO DO
# handle fields that have a type but no name

import os, sys

import asdl

TABSIZE = 8
MAX_COL = 80

def get_c_type(name):
    """Return a string for the C name of the type.

    This function special cases the default types provided by asdl:
    identifier, string, int, bool.
    """
    # XXX ack!  need to figure out where Id is useful and where string
    if isinstance(name, asdl.Id):
        name = name.value
    if name in asdl.builtin_types:
        return name
    else:
        return "%s_ty" % name

def reflow_lines(s, depth):
    """Reflow the line s indented depth tabs.

    Return a sequence of lines where no line extends beyond MAX_COL
    when properly indented.  The first line is properly indented based
    exclusively on depth * TABSIZE.  All following lines -- these are
    the reflowed lines generated by this function -- start at the same
    column as the first character beyond the opening { in the first
    line.
    """
    size = MAX_COL - depth * TABSIZE
    if len(s) < size:
        return [s]

    lines = []
    cur = s
    padding = ""
    while len(cur) > size:
        i = cur.rfind(' ', 0, size)
        # XXX this should be fixed for real
        if i == -1 and 'GeneratorExp' in cur:
            i = size + 3
        assert i != -1, "Impossible line %d to reflow: %r" % (size, s)
        lines.append(padding + cur[:i])
        if len(lines) == 1:
            # find new size based on brace
            j = cur.find('{', 0, i)
            if j >= 0:
                j += 2 # account for the brace and the space after it
                size -= j
                padding = " " * j
            else:
                j = cur.find('(', 0, i)
                if j >= 0:
                    j += 1 # account for the paren (no space after it)
                    size -= j
                    padding = " " * j
        cur = cur[i+1:]
    else:
        lines.append(padding + cur)
    return lines

def is_simple(sum):
    """Return True if a sum is a simple.

    A sum is simple if its types have no fields, e.g.
    unaryop = Invert | Not | UAdd | USub
    """
    for t in sum.types:
        if t.fields:
            return False
    return True


class EmitVisitor(asdl.VisitorBase):
    """Visit that emits lines"""

    def __init__(self, file):
        self.file = file
        super(EmitVisitor, self).__init__()

    def emit(self, s, depth, reflow=1):
        # XXX reflow long lines?
        if reflow:
            lines = reflow_lines(s, depth)
        else:
            lines = [s]
        for line in lines:
            line = (" " * TABSIZE * depth) + line + "\n"
            self.file.write(line)


class TypeDefVisitor(EmitVisitor):
    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type, depth=0):
        self.visit(type.value, type.name, depth)

    def visitSum(self, sum, name, depth):
        if is_simple(sum):
            self.simple_sum(sum, name, depth)
        else:
            self.sum_with_constructors(sum, name, depth)

    def simple_sum(self, sum, name, depth):
        enum = []
        for i in range(len(sum.types)):
            type = sum.types[i]
            enum.append("%s=%d" % (type.name, i + 1))
        enums = ", ".join(enum)
        ctype = get_c_type(name)
        s = "typedef enum _%s { %s } %s;" % (name, enums, ctype)
        self.emit(s, depth)
        self.emit("", depth)

    def sum_with_constructors(self, sum, name, depth):
        ctype = get_c_type(name)
        s = "typedef struct _%(name)s *%(ctype)s;" % locals()
        self.emit(s, depth)
        self.emit("", depth)

    def visitProduct(self, product, name, depth):
        ctype = get_c_type(name)
        s = "typedef struct _%(name)s *%(ctype)s;" % locals()
        self.emit(s, depth)
        self.emit("", depth)


class StructVisitor(EmitVisitor):
    """Visitor to generate typdefs for AST."""

    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type, depth=0):
        self.visit(type.value, type.name, depth)

    def visitSum(self, sum, name, depth):
        if not is_simple(sum):
            self.sum_with_constructors(sum, name, depth)

    def sum_with_constructors(self, sum, name, depth):
        def emit(s, depth=depth):
            self.emit(s % sys._getframe(1).f_locals, depth)
        enum = []
        for i in range(len(sum.types)):
            type = sum.types[i]
            enum.append("%s_kind=%d" % (type.name, i + 1))

        emit("enum _%(name)s_kind {" + ", ".join(enum) + "};")

        emit("struct _%(name)s {")
        emit("enum _%(name)s_kind kind;", depth + 1)
        emit("union {", depth + 1)
        for t in sum.types:
            self.visit(t, depth + 2)
        emit("} v;", depth + 1)
        for field in sum.attributes:
            # rudimentary attribute handling
            type = str(field.type)
            assert type in asdl.builtin_types, type
            emit("%s %s;" % (type, field.name), depth + 1);
        emit("};")
        emit("")

    def visitConstructor(self, cons, depth):
        if cons.fields:
            self.emit("struct {", depth)
            for f in cons.fields:
                self.visit(f, depth + 1)
            self.emit("} %s;" % cons.name, depth)
            self.emit("", depth)
        else:
            # XXX not sure what I want here, nothing is probably fine
            pass

    def visitField(self, field, depth):
        # XXX need to lookup field.type, because it might be something
        # like a builtin...
        ctype = get_c_type(field.type)
        name = field.name
        if field.seq:
            if field.type.value in ('cmpop',):
                self.emit("asdl_int_seq *%(name)s;" % locals(), depth)
            else:
                self.emit("asdl_seq *%(name)s;" % locals(), depth)
        else:
            self.emit("%(ctype)s %(name)s;" % locals(), depth)

    def visitProduct(self, product, name, depth):
        self.emit("struct _%(name)s {" % locals(), depth)
        for f in product.fields:
            self.visit(f, depth + 1)
        self.emit("};", depth)
        self.emit("", depth)


class PrototypeVisitor(EmitVisitor):
    """Generate function prototypes for the .h file"""

    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type):
        self.visit(type.value, type.name)

    def visitSum(self, sum, name):
        if is_simple(sum):
            pass # XXX
        else:
            for t in sum.types:
                self.visit(t, name, sum.attributes)

    def get_args(self, fields):
        """Return list of C argument into, one for each field.

        Argument info is 3-tuple of a C type, variable name, and flag
        that is true if type can be NULL.
        """
        args = []
        unnamed = {}
        for f in fields:
            if f.name is None:
                name = f.type
                c = unnamed[name] = unnamed.get(name, 0) + 1
                if c > 1:
                    name = "name%d" % (c - 1)
            else:
                name = f.name
            # XXX should extend get_c_type() to handle this
            if f.seq:
                if f.type.value in ('cmpop',):
                    ctype = "asdl_int_seq *"
                else:
                    ctype = "asdl_seq *"
            else:
                ctype = get_c_type(f.type)
            args.append((ctype, name, f.opt or f.seq))
        return args

    def visitConstructor(self, cons, type, attrs):
        args = self.get_args(cons.fields)
        attrs = self.get_args(attrs)
        ctype = get_c_type(type)
        self.emit_function(cons.name, ctype, args, attrs)

    def emit_function(self, name, ctype, args, attrs, union=1):
        args = args + attrs
        if args:
            argstr = ", ".join(["%s %s" % (atype, aname)
                                for atype, aname, opt in args])
            argstr += ", PyArena *arena"
        else:
            argstr = "PyArena *arena"
        margs = "a0"
        for i in range(1, len(args)+1):
            margs += ", a%d" % i
        self.emit("#define %s(%s) _Py_%s(%s)" % (name, margs, name, margs), 0,
                reflow = 0)
        self.emit("%s _Py_%s(%s);" % (ctype, name, argstr), 0)

    def visitProduct(self, prod, name):
        self.emit_function(name, get_c_type(name),
                           self.get_args(prod.fields), [], union=0)


class FunctionVisitor(PrototypeVisitor):
    """Visitor to generate constructor functions for AST."""

    def emit_function(self, name, ctype, args, attrs, union=1):
        def emit(s, depth=0, reflow=1):
            self.emit(s, depth, reflow)
        argstr = ", ".join(["%s %s" % (atype, aname)
                            for atype, aname, opt in args + attrs])
        if argstr:
            argstr += ", PyArena *arena"
        else:
            argstr = "PyArena *arena"
        self.emit("%s" % ctype, 0)
        emit("%s(%s)" % (name, argstr))
        emit("{")
        emit("%s p;" % ctype, 1)
        for argtype, argname, opt in args:
            # XXX hack alert: false is allowed for a bool
            if not opt and not (argtype == "bool" or argtype == "int"):
                emit("if (!%s) {" % argname, 1)
                emit("PyErr_SetString(PyExc_ValueError,", 2)
                msg = "field %s is required for %s" % (argname, name)
                emit('                "%s");' % msg,
                     2, reflow=0)
                emit('return NULL;', 2)
                emit('}', 1)

        emit("p = (%s)PyArena_Malloc(arena, sizeof(*p));" % ctype, 1);
        emit("if (!p)", 1)
        emit("return NULL;", 2)
        if union:
            self.emit_body_union(name, args, attrs)
        else:
            self.emit_body_struct(name, args, attrs)
        emit("return p;", 1)
        emit("}")
        emit("")

    def emit_body_union(self, name, args, attrs):
        def emit(s, depth=0, reflow=1):
            self.emit(s, depth, reflow)
        emit("p->kind = %s_kind;" % name, 1)
        for argtype, argname, opt in args:
            emit("p->v.%s.%s = %s;" % (name, argname, argname), 1)
        for argtype, argname, opt in attrs:
            emit("p->%s = %s;" % (argname, argname), 1)

    def emit_body_struct(self, name, args, attrs):
        def emit(s, depth=0, reflow=1):
            self.emit(s, depth, reflow)
        for argtype, argname, opt in args:
            emit("p->%s = %s;" % (argname, argname), 1)
        assert not attrs


class PickleVisitor(EmitVisitor):

    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type):
        self.visit(type.value, type.name)

    def visitSum(self, sum, name):
        pass

    def visitProduct(self, sum, name):
        pass

    def visitConstructor(self, cons, name):
        pass

    def visitField(self, sum):
        pass


class Obj2ModPrototypeVisitor(PickleVisitor):
    def visitProduct(self, prod, name):
        code = "static int obj2ast_%s(PyObject* obj, %s* out, PyArena* arena);"
        self.emit(code % (name, get_c_type(name)), 0)

    visitSum = visitProduct


class Obj2ModVisitor(PickleVisitor):
    def funcHeader(self, name):
        ctype = get_c_type(name)
        self.emit("int", 0)
        self.emit("obj2ast_%s(PyObject* obj, %s* out, PyArena* arena)" % (name, ctype), 0)
        self.emit("{", 0)
        self.emit("PyObject* tmp = NULL;", 1)
        self.emit("", 0)

    def sumTrailer(self, name):
        self.emit("", 0)
        self.emit("tmp = PyObject_Repr(obj);", 1)
        # there's really nothing more we can do if this fails ...
        self.emit("if (tmp == NULL) goto failed;", 1)
        error = "expected some sort of %s, but got %%.400s" % name
        format = "PyErr_Format(PyExc_TypeError, \"%s\", PyString_AS_STRING(tmp));"
        self.emit(format % error, 1, reflow=False)
        self.emit("failed:", 0)
        self.emit("Py_XDECREF(tmp);", 1)
        self.emit("return 1;", 1)
        self.emit("}", 0)
        self.emit("", 0)

    def simpleSum(self, sum, name):
        self.funcHeader(name)
        for t in sum.types:
            self.emit("if (PyObject_IsInstance(obj, (PyObject*)%s_type)) {" % t.name, 1)
            self.emit("*out = %s;" % t.name, 2)
            self.emit("return 0;", 2)
            self.emit("}", 1)
        self.sumTrailer(name)

    def buildArgs(self, fields):
        return ", ".join(fields + ["arena"])

    def complexSum(self, sum, name):
        self.funcHeader(name)
        for a in sum.attributes:
            self.visitAttributeDeclaration(a, name, sum=sum)
        self.emit("", 0)
        # XXX: should we only do this for 'expr'?
        self.emit("if (obj == Py_None) {", 1)
        self.emit("*out = NULL;", 2)
        self.emit("return 0;", 2)
        self.emit("}", 1)
        for a in sum.attributes:
            self.visitField(a, name, sum=sum, depth=1)
        for t in sum.types:
            self.emit("if (PyObject_IsInstance(obj, (PyObject*)%s_type)) {" % t.name, 1)
            for f in t.fields:
                self.visitFieldDeclaration(f, t.name, sum=sum, depth=2)
            self.emit("", 0)
            for f in t.fields:
                self.visitField(f, t.name, sum=sum, depth=2)
            args = [f.name.value for f in t.fields] + [a.name.value for a in sum.attributes]
            self.emit("*out = %s(%s);" % (t.name, self.buildArgs(args)), 2)
            self.emit("if (*out == NULL) goto failed;", 2)
            self.emit("return 0;", 2)
            self.emit("}", 1)
        self.sumTrailer(name)

    def visitAttributeDeclaration(self, a, name, sum=sum):
        ctype = get_c_type(a.type)
        self.emit("%s %s;" % (ctype, a.name), 1)

    def visitSum(self, sum, name):
        if is_simple(sum):
            self.simpleSum(sum, name)
        else:
            self.complexSum(sum, name)

    def visitProduct(self, prod, name):
        ctype = get_c_type(name)
        self.emit("int", 0)
        self.emit("obj2ast_%s(PyObject* obj, %s* out, PyArena* arena)" % (name, ctype), 0)
        self.emit("{", 0)
        self.emit("PyObject* tmp = NULL;", 1)
        for f in prod.fields:
            self.visitFieldDeclaration(f, name, prod=prod, depth=1)
        self.emit("", 0)
        for f in prod.fields:
            self.visitField(f, name, prod=prod, depth=1)
        args = [f.name.value for f in prod.fields]
        self.emit("*out = %s(%s);" % (name, self.buildArgs(args)), 1)
        self.emit("return 0;", 1)
        self.emit("failed:", 0)
        self.emit("Py_XDECREF(tmp);", 1)
        self.emit("return 1;", 1)
        self.emit("}", 0)
        self.emit("", 0)

    def visitFieldDeclaration(self, field, name, sum=None, prod=None, depth=0):
        ctype = get_c_type(field.type)
        if field.seq:
            if self.isSimpleType(field):
                self.emit("asdl_int_seq* %s;" % field.name, depth)
            else:
                self.emit("asdl_seq* %s;" % field.name, depth)
        else:
            ctype = get_c_type(field.type)
            self.emit("%s %s;" % (ctype, field.name), depth)

    def isSimpleSum(self, field):
        # XXX can the members of this list be determined automatically?
        return field.type.value in ('expr_context', 'boolop', 'operator',
                                    'unaryop', 'cmpop')

    def isNumeric(self, field):
        return get_c_type(field.type) in ("int", "bool")

    def isSimpleType(self, field):
        return self.isSimpleSum(field) or self.isNumeric(field)

    def visitField(self, field, name, sum=None, prod=None, depth=0):
        ctype = get_c_type(field.type)
        self.emit("if (PyObject_HasAttrString(obj, \"%s\")) {" % field.name, depth)
        self.emit("int res;", depth+1)
        if field.seq:
            self.emit("Py_ssize_t len;", depth+1)
            self.emit("Py_ssize_t i;", depth+1)
        self.emit("tmp = PyObject_GetAttrString(obj, \"%s\");" % field.name, depth+1)
        self.emit("if (tmp == NULL) goto failed;", depth+1)
        if field.seq:
            self.emit("if (!PyList_Check(tmp)) {", depth+1)
            self.emit("PyErr_Format(PyExc_TypeError, \"%s field \\\"%s\\\" must "
                      "be a list, not a %%.200s\", tmp->ob_type->tp_name);" %
                      (name, field.name),
                      depth+2, reflow=False)
            self.emit("goto failed;", depth+2)
            self.emit("}", depth+1)
            self.emit("len = PyList_GET_SIZE(tmp);", depth+1)
            if self.isSimpleType(field):
                self.emit("%s = asdl_int_seq_new(len, arena);" % field.name, depth+1)
            else:
                self.emit("%s = asdl_seq_new(len, arena);" % field.name, depth+1)
            self.emit("if (%s == NULL) goto failed;" % field.name, depth+1)
            self.emit("for (i = 0; i < len; i++) {", depth+1)
            self.emit("%s value;" % ctype, depth+2)
            self.emit("res = obj2ast_%s(PyList_GET_ITEM(tmp, i), &value, arena);" %
                      field.type, depth+2, reflow=False)
            self.emit("if (res != 0) goto failed;", depth+2)
            self.emit("asdl_seq_SET(%s, i, value);" % field.name, depth+2)
            self.emit("}", depth+1)
        else:
            self.emit("res = obj2ast_%s(tmp, &%s, arena);" %
                      (field.type, field.name), depth+1)
            self.emit("if (res != 0) goto failed;", depth+1)

        self.emit("Py_XDECREF(tmp);", depth+1)
        self.emit("tmp = NULL;", depth+1)
        self.emit("} else {", depth)
        if not field.opt:
            message = "required field \\\"%s\\\" missing from %s" % (field.name, name)
            format = "PyErr_SetString(PyExc_TypeError, \"%s\");"
            self.emit(format % message, depth+1, reflow=False)
            self.emit("return 1;", depth+1)
        else:
            if self.isNumeric(field):
                self.emit("%s = 0;" % field.name, depth+1)
            elif not self.isSimpleType(field):
                self.emit("%s = NULL;" % field.name, depth+1)
            else:
                raise TypeError("could not determine the default value for %s" % field.name)
        self.emit("}", depth)


class MarshalPrototypeVisitor(PickleVisitor):

    def prototype(self, sum, name):
        ctype = get_c_type(name)
        self.emit("static int marshal_write_%s(PyObject **, int *, %s);"
                  % (name, ctype), 0)

    visitProduct = visitSum = prototype


class PyTypesDeclareVisitor(PickleVisitor):

    def visitProduct(self, prod, name):
        self.emit("static PyTypeObject *%s_type;" % name, 0)
        self.emit("static PyObject* ast2obj_%s(void*);" % name, 0)
        if prod.fields:
            self.emit("static char *%s_fields[]={" % name,0)
            for f in prod.fields:
                self.emit('"%s",' % f.name, 1)
            self.emit("};", 0)

    def visitSum(self, sum, name):
        self.emit("static PyTypeObject *%s_type;" % name, 0)
        if sum.attributes:
            self.emit("static char *%s_attributes[] = {" % name, 0)
            for a in sum.attributes:
                self.emit('"%s",' % a.name, 1)
            self.emit("};", 0)
        ptype = "void*"
        if is_simple(sum):
            ptype = get_c_type(name)
            tnames = []
            for t in sum.types:
                tnames.append(str(t.name)+"_singleton")
            tnames = ", *".join(tnames)
            self.emit("static PyObject *%s;" % tnames, 0)
        self.emit("static PyObject* ast2obj_%s(%s);" % (name, ptype), 0)
        for t in sum.types:
            self.visitConstructor(t, name)

    def visitConstructor(self, cons, name):
        self.emit("static PyTypeObject *%s_type;" % cons.name, 0)
        if cons.fields:
            self.emit("static char *%s_fields[]={" % cons.name, 0)
            for t in cons.fields:
                self.emit('"%s",' % t.name, 1)
            self.emit("};",0)

class PyTypesVisitor(PickleVisitor):

    def visitModule(self, mod):
        self.emit("""
static int
ast_type_init(PyObject *self, PyObject *args, PyObject *kw)
{
    Py_ssize_t i, numfields = 0;
    int res = -1;
    PyObject *key, *value, *fields;
    fields = PyObject_GetAttrString((PyObject*)Py_TYPE(self), "_fields");
    if (!fields)
        PyErr_Clear();
    if (fields) {
        numfields = PySequence_Size(fields);
        if (numfields == -1)
            goto cleanup;
    }
    res = 0; /* if no error occurs, this stays 0 to the end */
    if (PyTuple_GET_SIZE(args) > 0) {
        if (numfields != PyTuple_GET_SIZE(args)) {
            PyErr_Format(PyExc_TypeError, "%.400s constructor takes either 0 or "
                         "%d positional argument%s", Py_TYPE(self)->tp_name,
                         numfields, numfields == 1 ? "" : "s");
            res = -1;
            goto cleanup;
        }
        for (i = 0; i < PyTuple_GET_SIZE(args); i++) {
            /* cannot be reached when fields is NULL */
            PyObject *name = PySequence_GetItem(fields, i);
            if (!name) {
                res = -1;
                goto cleanup;
            }
            res = PyObject_SetAttr(self, name, PyTuple_GET_ITEM(args, i));
            Py_DECREF(name);
            if (res < 0)
                goto cleanup;
        }
    }
    if (kw) {
        i = 0;  /* needed by PyDict_Next */
        while (PyDict_Next(kw, &i, &key, &value)) {
            res = PyObject_SetAttr(self, key, value);
            if (res < 0)
                goto cleanup;
        }
    }
  cleanup:
    Py_XDECREF(fields);
    return res;
}

static PyTypeObject AST_type = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "AST",
    sizeof(PyObject),
    0,
    0,                       /* tp_dealloc */
    0,                       /* tp_print */
    0,                       /* tp_getattr */
    0,                       /* tp_setattr */
    0,                       /* tp_compare */
    0,                       /* tp_repr */
    0,                       /* tp_as_number */
    0,                       /* tp_as_sequence */
    0,                       /* tp_as_mapping */
    0,                       /* tp_hash */
    0,                       /* tp_call */
    0,                       /* tp_str */
    PyObject_GenericGetAttr, /* tp_getattro */
    PyObject_GenericSetAttr, /* tp_setattro */
    0,                       /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /* tp_flags */
    0,                       /* tp_doc */
    0,                       /* tp_traverse */
    0,                       /* tp_clear */
    0,                       /* tp_richcompare */
    0,                       /* tp_weaklistoffset */
    0,                       /* tp_iter */
    0,                       /* tp_iternext */
    0,                       /* tp_methods */
    0,                       /* tp_members */
    0,                       /* tp_getset */
    0,                       /* tp_base */
    0,                       /* tp_dict */
    0,                       /* tp_descr_get */
    0,                       /* tp_descr_set */
    0,                       /* tp_dictoffset */
    (initproc)ast_type_init, /* tp_init */
    PyType_GenericAlloc,     /* tp_alloc */
    PyType_GenericNew,       /* tp_new */
    PyObject_Del,            /* tp_free */
};


static PyTypeObject* make_type(char *type, PyTypeObject* base, char**fields, int num_fields)
{
    PyObject *fnames, *result;
    int i;
    if (num_fields) {
        fnames = PyTuple_New(num_fields);
        if (!fnames) return NULL;
    } else {
        fnames = Py_None;
        Py_INCREF(Py_None);
    }
    for(i=0; i < num_fields; i++) {
        PyObject *field = PyString_FromString(fields[i]);
        if (!field) {
            Py_DECREF(fnames);
            return NULL;
        }
        PyTuple_SET_ITEM(fnames, i, field);
    }
    result = PyObject_CallFunction((PyObject*)&PyType_Type, "s(O){sOss}",
                    type, base, "_fields", fnames, "__module__", "_ast");
    Py_DECREF(fnames);
    return (PyTypeObject*)result;
}

static int add_attributes(PyTypeObject* type, char**attrs, int num_fields)
{
    int i, result;
    PyObject *s, *l = PyTuple_New(num_fields);
    if (!l) return 0;
    for(i = 0; i < num_fields; i++) {
        s = PyString_FromString(attrs[i]);
        if (!s) {
            Py_DECREF(l);
            return 0;
        }
        PyTuple_SET_ITEM(l, i, s);
    }
    result = PyObject_SetAttrString((PyObject*)type, "_attributes", l) >= 0;
    Py_DECREF(l);
    return result;
}

/* Conversion AST -> Python */

static PyObject* ast2obj_list(asdl_seq *seq, PyObject* (*func)(void*))
{
    int i, n = asdl_seq_LEN(seq);
    PyObject *result = PyList_New(n);
    PyObject *value;
    if (!result)
        return NULL;
    for (i = 0; i < n; i++) {
        value = func(asdl_seq_GET(seq, i));
        if (!value) {
            Py_DECREF(result);
            return NULL;
        }
        PyList_SET_ITEM(result, i, value);
    }
    return result;
}

static PyObject* ast2obj_object(void *o)
{
    if (!o)
        o = Py_None;
    Py_INCREF((PyObject*)o);
    return (PyObject*)o;
}
#define ast2obj_identifier ast2obj_object
#define ast2obj_string ast2obj_object
static PyObject* ast2obj_bool(bool b)
{
    return PyBool_FromLong(b);
}

static PyObject* ast2obj_int(long b)
{
    return PyInt_FromLong(b);
}

/* Conversion Python -> AST */

static int obj2ast_object(PyObject* obj, PyObject** out, PyArena* arena)
{
    if (obj == Py_None)
        obj = NULL;
    if (obj)
        PyArena_AddPyObject(arena, obj);
    Py_XINCREF(obj);
    *out = obj;
    return 0;
}

#define obj2ast_identifier obj2ast_object
#define obj2ast_string obj2ast_object

static int obj2ast_int(PyObject* obj, int* out, PyArena* arena)
{
    int i;
    if (!PyInt_Check(obj) && !PyLong_Check(obj)) {
        PyObject *s = PyObject_Repr(obj);
        if (s == NULL) return 1;
        PyErr_Format(PyExc_ValueError, "invalid integer value: %.400s",
                     PyString_AS_STRING(s));
        Py_DECREF(s);
        return 1;
    }

    i = (int)PyLong_AsLong(obj);
    if (i == -1 && PyErr_Occurred())
        return 1;
    *out = i;
    return 0;
}

static int obj2ast_bool(PyObject* obj, bool* out, PyArena* arena)
{
    if (!PyBool_Check(obj)) {
        PyObject *s = PyObject_Repr(obj);
        if (s == NULL) return 1;
        PyErr_Format(PyExc_ValueError, "invalid boolean value: %.400s",
                     PyString_AS_STRING(s));
        Py_DECREF(s);
        return 1;
    }

    *out = (obj == Py_True);
    return 0;
}

""", 0, reflow=False)

        self.emit("static int init_types(void)",0)
        self.emit("{", 0)
        self.emit("static int initialized;", 1)
        self.emit("if (initialized) return 1;", 1)
        for dfn in mod.dfns:
            self.visit(dfn)
        self.emit("initialized = 1;", 1)
        self.emit("return 1;", 1);
        self.emit("}", 0)

    def visitProduct(self, prod, name):
        if prod.fields:
            fields = name.value+"_fields"
        else:
            fields = "NULL"
        self.emit('%s_type = make_type("%s", &AST_type, %s, %d);' %
                        (name, name, fields, len(prod.fields)), 1)
        self.emit("if (!%s_type) return 0;" % name, 1)

    def visitSum(self, sum, name):
        self.emit('%s_type = make_type("%s", &AST_type, NULL, 0);' %
                  (name, name), 1)
        self.emit("if (!%s_type) return 0;" % name, 1)
        if sum.attributes:
            self.emit("if (!add_attributes(%s_type, %s_attributes, %d)) return 0;" %
                            (name, name, len(sum.attributes)), 1)
        else:
            self.emit("if (!add_attributes(%s_type, NULL, 0)) return 0;" % name, 1)
        simple = is_simple(sum)
        for t in sum.types:
            self.visitConstructor(t, name, simple)

    def visitConstructor(self, cons, name, simple):
        if cons.fields:
            fields = cons.name.value+"_fields"
        else:
            fields = "NULL"
        self.emit('%s_type = make_type("%s", %s_type, %s, %d);' %
                            (cons.name, cons.name, name, fields, len(cons.fields)), 1)
        self.emit("if (!%s_type) return 0;" % cons.name, 1)
        if simple:
            self.emit("%s_singleton = PyType_GenericNew(%s_type, NULL, NULL);" %
                             (cons.name, cons.name), 1)
            self.emit("if (!%s_singleton) return 0;" % cons.name, 1)


def parse_version(mod):
    return mod.version.value[12:-3]

class ASTModuleVisitor(PickleVisitor):

    def visitModule(self, mod):
        self.emit("PyMODINIT_FUNC", 0)
        self.emit("init_ast(void)", 0)
        self.emit("{", 0)
        self.emit("PyObject *m, *d;", 1)
        self.emit("if (!init_types()) return;", 1)
        self.emit('m = Py_InitModule3("_ast", NULL, NULL);', 1)
        self.emit("if (!m) return;", 1)
        self.emit("d = PyModule_GetDict(m);", 1)
        self.emit('if (PyDict_SetItemString(d, "AST", (PyObject*)&AST_type) < 0) return;', 1)
        self.emit('if (PyModule_AddIntConstant(m, "PyCF_ONLY_AST", PyCF_ONLY_AST) < 0)', 1)
        self.emit("return;", 2)
        # Value of version: "$Revision$"
        self.emit('if (PyModule_AddStringConstant(m, "__version__", "%s") < 0)'
                % parse_version(mod), 1)
        self.emit("return;", 2)
        for dfn in mod.dfns:
            self.visit(dfn)
        self.emit("}", 0)

    def visitProduct(self, prod, name):
        self.addObj(name)

    def visitSum(self, sum, name):
        self.addObj(name)
        for t in sum.types:
            self.visitConstructor(t, name)

    def visitConstructor(self, cons, name):
        self.addObj(cons.name)

    def addObj(self, name):
        self.emit('if (PyDict_SetItemString(d, "%s", (PyObject*)%s_type) < 0) return;' % (name, name), 1)


_SPECIALIZED_SEQUENCES = ('stmt', 'expr')

def find_sequence(fields, doing_specialization):
    """Return True if any field uses a sequence."""
    for f in fields:
        if f.seq:
            if not doing_specialization:
                return True
            if str(f.type) not in _SPECIALIZED_SEQUENCES:
                return True
    return False

def has_sequence(types, doing_specialization):
    for t in types:
        if find_sequence(t.fields, doing_specialization):
            return True
    return False


class StaticVisitor(PickleVisitor):
    CODE = '''Very simple, always emit this static code.  Overide CODE'''

    def visit(self, object):
        self.emit(self.CODE, 0, reflow=False)


class ObjVisitor(PickleVisitor):

    def func_begin(self, name):
        ctype = get_c_type(name)
        self.emit("PyObject*", 0)
        self.emit("ast2obj_%s(void* _o)" % (name), 0)
        self.emit("{", 0)
        self.emit("%s o = (%s)_o;" % (ctype, ctype), 1)
        self.emit("PyObject *result = NULL, *value = NULL;", 1)
        self.emit('if (!o) {', 1)
        self.emit("Py_INCREF(Py_None);", 2)
        self.emit('return Py_None;', 2)
        self.emit("}", 1)
        self.emit('', 0)

    def func_end(self):
        self.emit("return result;", 1)
        self.emit("failed:", 0)
        self.emit("Py_XDECREF(value);", 1)
        self.emit("Py_XDECREF(result);", 1)
        self.emit("return NULL;", 1)
        self.emit("}", 0)
        self.emit("", 0)

    def visitSum(self, sum, name):
        if is_simple(sum):
            self.simpleSum(sum, name)
            return
        self.func_begin(name)
        self.emit("switch (o->kind) {", 1)
        for i in range(len(sum.types)):
            t = sum.types[i]
            self.visitConstructor(t, i + 1, name)
        self.emit("}", 1)
        for a in sum.attributes:
            self.emit("value = ast2obj_%s(o->%s);" % (a.type, a.name), 1)
            self.emit("if (!value) goto failed;", 1)
            self.emit('if (PyObject_SetAttrString(result, "%s", value) < 0)' % a.name, 1)
            self.emit('goto failed;', 2)
            self.emit('Py_DECREF(value);', 1)
        self.func_end()

    def simpleSum(self, sum, name):
        self.emit("PyObject* ast2obj_%s(%s_ty o)" % (name, name), 0)
        self.emit("{", 0)
        self.emit("switch(o) {", 1)
        for t in sum.types:
            self.emit("case %s:" % t.name, 2)
            self.emit("Py_INCREF(%s_singleton);" % t.name, 3)
            self.emit("return %s_singleton;" % t.name, 3)
        self.emit("default:" % name, 2)
        self.emit('/* should never happen, but just in case ... */', 3)
        code = "PyErr_Format(PyExc_SystemError, \"unknown %s found\");" % name
        self.emit(code, 3, reflow=False)
        self.emit("return NULL;", 3)
        self.emit("}", 1)
        self.emit("}", 0)

    def visitProduct(self, prod, name):
        self.func_begin(name)
        self.emit("result = PyType_GenericNew(%s_type, NULL, NULL);" % name, 1);
        self.emit("if (!result) return NULL;", 1)
        for field in prod.fields:
            self.visitField(field, name, 1, True)
        self.func_end()

    def visitConstructor(self, cons, enum, name):
        self.emit("case %s_kind:" % cons.name, 1)
        self.emit("result = PyType_GenericNew(%s_type, NULL, NULL);" % cons.name, 2);
        self.emit("if (!result) goto failed;", 2)
        for f in cons.fields:
            self.visitField(f, cons.name, 2, False)
        self.emit("break;", 2)

    def visitField(self, field, name, depth, product):
        def emit(s, d):
            self.emit(s, depth + d)
        if product:
            value = "o->%s" % field.name
        else:
            value = "o->v.%s.%s" % (name, field.name)
        self.set(field, value, depth)
        emit("if (!value) goto failed;", 0)
        emit('if (PyObject_SetAttrString(result, "%s", value) == -1)' % field.name, 0)
        emit("goto failed;", 1)
        emit("Py_DECREF(value);", 0)

    def emitSeq(self, field, value, depth, emit):
        emit("seq = %s;" % value, 0)
        emit("n = asdl_seq_LEN(seq);", 0)
        emit("value = PyList_New(n);", 0)
        emit("if (!value) goto failed;", 0)
        emit("for (i = 0; i < n; i++) {", 0)
        self.set("value", field, "asdl_seq_GET(seq, i)", depth + 1)
        emit("if (!value1) goto failed;", 1)
        emit("PyList_SET_ITEM(value, i, value1);", 1)
        emit("value1 = NULL;", 1)
        emit("}", 0)

    def set(self, field, value, depth):
        if field.seq:
            # XXX should really check for is_simple, but that requires a symbol table
            if field.type.value == "cmpop":
                # While the sequence elements are stored as void*,
                # ast2obj_cmpop expects an enum
                self.emit("{", depth)
                self.emit("int i, n = asdl_seq_LEN(%s);" % value, depth+1)
                self.emit("value = PyList_New(n);", depth+1)
                self.emit("if (!value) goto failed;", depth+1)
                self.emit("for(i = 0; i < n; i++)", depth+1)
                # This cannot fail, so no need for error handling
                self.emit("PyList_SET_ITEM(value, i, ast2obj_cmpop((cmpop_ty)asdl_seq_GET(%s, i)));" % value,
                          depth+2, reflow=False)
                self.emit("}", depth)
            else:
                self.emit("value = ast2obj_list(%s, ast2obj_%s);" % (value, field.type), depth)
        else:
            ctype = get_c_type(field.type)
            self.emit("value = ast2obj_%s(%s);" % (field.type, value), depth, reflow=False)


class PartingShots(StaticVisitor):

    CODE = """
PyObject* PyAST_mod2obj(mod_ty t)
{
    init_types();
    return ast2obj_mod(t);
}

/* mode is 0 for "exec", 1 for "eval" and 2 for "single" input */
mod_ty PyAST_obj2mod(PyObject* ast, PyArena* arena, int mode)
{
    mod_ty res;
    PyObject *req_type[] = {(PyObject*)Module_type, (PyObject*)Expression_type,
                            (PyObject*)Interactive_type};
    char *req_name[] = {"Module", "Expression", "Interactive"};
    assert(0 <= mode && mode <= 2);

    init_types();

    if (!PyObject_IsInstance(ast, req_type[mode])) {
        PyErr_Format(PyExc_TypeError, "expected %s node, got %.400s",
                     req_name[mode], Py_TYPE(ast)->tp_name);
        return NULL;
    }
    if (obj2ast_mod(ast, &res, arena) != 0)
        return NULL;
    else
        return res;
}

int PyAST_Check(PyObject* obj)
{
    init_types();
    return PyObject_IsInstance(obj, (PyObject*)&AST_type);
}
"""

class ChainOfVisitors:
    def __init__(self, *visitors):
        self.visitors = visitors

    def visit(self, object):
        for v in self.visitors:
            v.visit(object)
            v.emit("", 0)

common_msg = "/* File automatically generated by %s. */\n"

c_file_msg = """
/*
   __version__ %s.

   This module must be committed separately after each AST grammar change;
   The __version__ number is set to the revision number of the commit
   containing the grammar change.
*/
"""

def main(srcfile):
    argv0 = sys.argv[0]
    components = argv0.split(os.sep)
    argv0 = os.sep.join(components[-2:])
    auto_gen_msg = common_msg % argv0
    mod = asdl.parse(srcfile)
    if not asdl.check(mod):
        sys.exit(1)
    if INC_DIR:
        p = "%s/%s-ast.h" % (INC_DIR, mod.name)
        f = open(p, "wb")
        print >> f, auto_gen_msg
        print >> f, '#include "asdl.h"\n'
        c = ChainOfVisitors(TypeDefVisitor(f),
                            StructVisitor(f),
                            PrototypeVisitor(f),
                            )
        c.visit(mod)
        print >>f, "PyObject* PyAST_mod2obj(mod_ty t);"
        print >>f, "mod_ty PyAST_obj2mod(PyObject* ast, PyArena* arena, int mode);"
        print >>f, "int PyAST_Check(PyObject* obj);"
        f.close()

    if SRC_DIR:
        p = os.path.join(SRC_DIR, str(mod.name) + "-ast.c")
        f = open(p, "wb")
        print >> f, auto_gen_msg
        print >> f, c_file_msg % parse_version(mod)
        print >> f, '#include "Python.h"'
        print >> f, '#include "%s-ast.h"' % mod.name
        print >> f
        print >>f, "static PyTypeObject AST_type;"
        v = ChainOfVisitors(
            PyTypesDeclareVisitor(f),
            PyTypesVisitor(f),
            Obj2ModPrototypeVisitor(f),
            FunctionVisitor(f),
            ObjVisitor(f),
            Obj2ModVisitor(f),
            ASTModuleVisitor(f),
            PartingShots(f),
            )
        v.visit(mod)
        f.close()

if __name__ == "__main__":
    import sys
    import getopt

    INC_DIR = ''
    SRC_DIR = ''
    opts, args = getopt.getopt(sys.argv[1:], "h:c:")
    if len(opts) != 1:
        print "Must specify exactly one output file"
        sys.exit(1)
    for o, v in opts:
        if o == '-h':
            INC_DIR = v
        if o == '-c':
            SRC_DIR = v
    if len(args) != 1:
        print "Must specify single input file"
        sys.exit(1)
    main(args[0])
