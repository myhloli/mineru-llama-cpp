#pragma once

#include <Python.h>

#include <string>

namespace mineru_llama_cpp {

// 长度限制可能在多字节字符尚未补齐时终止生成；只丢弃末尾未完成的字节，
// 保留完整前缀和零字节。中间的非法 UTF-8 及非长度停止仍按严格解码报错。
// 返回 Unicode 新引用；失败时返回 nullptr 并保留解码异常。
inline PyObject * decode_output_content(const std::string & content,
                                          const std::string & finish_reason) {
    if (finish_reason != "length") {
        return PyUnicode_DecodeUTF8(content.data(), static_cast<Py_ssize_t>(content.size()), "strict");
    }

    Py_ssize_t consumed = 0;
    return PyUnicode_DecodeUTF8Stateful(
        content.data(), static_cast<Py_ssize_t>(content.size()), "strict", &consumed);
}

} // namespace mineru_llama_cpp
