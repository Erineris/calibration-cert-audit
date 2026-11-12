# -*- coding: utf-8 -*-
"""兼容薄壳：python3 -m streamlit run app/ui.py 仍可用，实际入口是 app/审核台.py。"""
import os, runpy
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "审核台.py"), run_name="__main__")
