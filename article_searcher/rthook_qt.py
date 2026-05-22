import os
import sys

if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
    if os.path.isdir(os.path.join(base_dir, '_internal')):
        base_dir = os.path.join(base_dir, '_internal')

    qt_plugin_path = os.path.join(base_dir, 'PyQt6', 'Qt6', 'plugins', 'platforms')
    if os.path.isdir(qt_plugin_path):
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_plugin_path

    qt_lib_path = os.path.join(base_dir, 'PyQt6', 'Qt6', 'bin')
    if os.path.isdir(qt_lib_path):
        os.add_dll_directory(qt_lib_path)
        os.environ['PATH'] = qt_lib_path + os.pathsep + os.environ.get('PATH', '')

    torch_lib_path = os.path.join(base_dir, 'torch', 'lib')
    if os.path.isdir(torch_lib_path):
        os.add_dll_directory(torch_lib_path)
        os.environ['PATH'] = torch_lib_path + os.pathsep + os.environ.get('PATH', '')

    try:
        import torch._C
    except Exception as e:
        print(f"Warning: Could not pre-import torch._C: {e}")

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
