#define PY_SSIZE_T_CLEAN
#include "output_utf8.h"

// 仅测试模块暴露原始字节解码，不向正式扩展添加测试接口。
static PyObject * decode(PyObject *, PyObject * args) {
    const char * bytes = nullptr, * reason = nullptr;
    Py_ssize_t size = 0;
    if (!PyArg_ParseTuple(args, "y#s", &bytes, &size, &reason)) return nullptr;
    return mineru_llama_cpp::decode_output_content(std::string(bytes, size), reason);
}
static PyMethodDef methods[] = {{"decode", decode, METH_VARARGS, "验证生产 UTF-8 解码。"}, {nullptr, nullptr, 0, nullptr}};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_utf8_decode_test", nullptr, -1, methods};
// 初始化临时 abi3 测试模块。
PyMODINIT_FUNC PyInit__utf8_decode_test() { return PyModule_Create(&module); }
