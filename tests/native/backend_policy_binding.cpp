#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "backend_policy.h"

// 将测试描述转换为生产策略的设备列表，返回选中的设备索引。
static PyObject * select_devices(PyObject *, PyObject * args) {
    PyObject * input = nullptr;
    const char * requested = nullptr;
    int force_cpu = 0;
    if (!PyArg_ParseTuple(args, "Osp", &input, &requested, &force_cpu)) return nullptr;
    try {
        std::vector<mineru_llama_cpp::BackendDevice> devices;
        const auto count = PyList_Size(input);
        if (count < 0) return nullptr;
        for (Py_ssize_t index = 0; index < count; ++index) {
            const char * backend = nullptr;
            int integrated = 0;
            Py_ssize_t device_index = 0;
            if (!PyArg_ParseTuple(PyList_GetItem(input, index), "spn", &backend, &integrated, &device_index)) return nullptr;
            devices.push_back({backend, integrated != 0, static_cast<size_t>(device_index)});
        }
        const auto selected = mineru_llama_cpp::select_backend_devices(devices, requested, force_cpu != 0);
        PyObject * result = PyList_New(selected.size());
        if (!result) return nullptr;
        for (size_t index = 0; index < selected.size(); ++index) {
            PyObject * value = PyLong_FromSize_t(selected[index]);
            if (!value || PyList_SetItem(result, index, value) < 0) { Py_DECREF(result); return nullptr; }
        }
        return result;
    } catch (const std::invalid_argument & error) { PyErr_SetString(PyExc_ValueError, error.what()); }
      catch (const std::exception & error) { PyErr_SetString(PyExc_RuntimeError, error.what()); }
    return nullptr;
}
static PyMethodDef methods[] = {{"select", select_devices, METH_VARARGS, "验证生产后端选择。"}, {nullptr, nullptr, 0, nullptr}};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_backend_policy_test", nullptr, -1, methods};
// 初始化临时 abi3 策略测试模块。
PyMODINIT_FUNC PyInit__backend_policy_test() { return PyModule_Create(&module); }
