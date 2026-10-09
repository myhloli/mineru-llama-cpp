// 模拟运行库存在但驱动初始化抛出异常的可选 MODULE。
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdexcept>
#ifdef _WIN32
#define BACKEND_EXPORT __declspec(dllexport)
#else
#define BACKEND_EXPORT __attribute__((visibility("default")))
#endif

// 让加载器进入初始化路径，而非在能力评分阶段跳过。
extern "C" BACKEND_EXPORT int ggml_backend_score() { return 1; }

// 用标准异常模拟驱动枚举失败，验证不会越过 C 边界终止进程。
extern "C" BACKEND_EXPORT void * ggml_backend_init() {
    throw std::runtime_error("optional driver unavailable (test fixture)");
}

// 满足测试扩展的链接入口；该文件只由 ggml 加载，不作为 Python 模块导入。
PyMODINIT_FUNC PyInit__optional_backend_test() {
    PyErr_SetString(PyExc_RuntimeError, "load this fixture through ggml, not Python");
    return nullptr;
}
