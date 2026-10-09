#pragma once

#include <pybind11/pybind11.h>

#include <string>

namespace mineru_llama_cpp {

// 长度限制可能在多字节字符尚未补齐时终止生成；只丢弃末尾未完成的字节，
// 保留完整前缀和零字节。中间的非法 UTF-8 及非长度停止仍按严格解码报错。
inline pybind11::str decode_output_content(const std::string & content,
                                          const std::string & finish_reason) {
    if (finish_reason != "length") {
        return pybind11::str(content);
    }

    Py_ssize_t consumed = 0;
    PyObject * decoded = PyUnicode_DecodeUTF8Stateful(
        content.data(), static_cast<Py_ssize_t>(content.size()), "strict", &consumed);
    if (decoded == nullptr) {
        throw pybind11::error_already_set();
    }
    // 解码器返回新引用，由 pybind11 接管，避免返回结果时泄漏 Python 对象。
    return pybind11::reinterpret_steal<pybind11::str>(decoded);
}

} // namespace mineru_llama_cpp
