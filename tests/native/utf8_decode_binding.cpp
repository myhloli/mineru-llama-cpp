#include "output_utf8.h"

// 仅在测试目录编译此模块，直接测试生产解码函数，不向正式扩展添加测试接口。
PYBIND11_MODULE(_utf8_decode_test, m) {
    m.def("decode", &mineru_llama_cpp::decode_output_content);
}
