"""把 test/unit 自身加进 sys.path。

`_fake_system_kv.py` 是跨目录共用的测试替身助手（memory/、self_evolution/、
services/ 都要用）。pytest 的默认 prepend 导入模式只会把**测试文件所在目录**
插进 sys.path，跨一层目录就 import 不到，所以这里补一次。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
