#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双色球推荐工具 - 图形界面版 v2.0.0
Python 3.12 兼容 | 支持Windows打包

新增功能：
1. 多模型推荐系统（8种算法）
2. 算法选择下拉菜单
3. 算法说明和参数配置
"""

try:
    import matplotlib
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print("=" * 60)
    print("错误:缺少必要的可视化库")
    print("=" * 60)
    print("请安装matplotlib:")
    print(" pip install matplotlib")
    print("=" * 60)
    exit()


import os
import json
import random
import requests
import threading
import queue
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# ==================== 配置模块 ====================


@dataclass
class AppConfig:
    """应用配置"""
    API_URL = "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&issueCount=&issueStart=&issueEnd=&dayStart=&dayEnd=&pageNo=1&pageSize=3000&week=&systemType=PC"
    CACHE_FILE = "ssq_history_cache.json"
    CACHE_EXPIRY_DAYS = 7
    RECOMMEND_COUNT = 5
    RED_BALL_RANGE = (1, 33)
    BLUE_BALL_RANGE = (1, 16)
    TIMEOUT = 30
    VERSION = "2.0.0"

    # UI配置
    WINDOW_SIZE = "980x720"  # 增加高度
    FONT_FAMILY = "Microsoft YaHei"
    FONT_FAMILY_MONO = "Consolas"

# 算法枚举


class RecommendAlgorithm(Enum):
    """推荐算法枚举"""
    FREQUENCY_WEIGHTED = ("frequency_weighted", "频率加权+随机（当前）")
    PURE_RANDOM = ("pure_random", "纯随机")
    PURE_FREQUENCY = ("pure_frequency", "纯频率")
    HOT_COLD_BALANCE = ("hot_cold_balance", "冷热平衡")
    INTERVAL_DISTRIBUTION = ("interval_distribution", "区间分布")
    ODD_EVEN_BALANCE = ("odd_even_balance", "奇偶平衡")
    SUM_OPTIMIZED = ("sum_optimized", "和值优化")
    NO_CONSECUTIVE = ("no_consecutive", "避免连号")

    @property
    def key(self):
        return self.value[0]

    @property
    def description(self):
        return self.value[1]

# 消息类型枚举


class MessageType(Enum):
    """线程通信消息类型"""
    FETCH_SUCCESS = "fetch_success"
    FETCH_ERROR = "fetch_error"
    RECOMMEND_SUCCESS = "recommend_success"
    ERROR = "error"
    PROGRESS_START = "progress_start"
    PROGRESS_STOP = "progress_stop"

# ==================== 日志配置 ====================


def setup_logging():
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )

# ==================== 核心逻辑模块 ====================


class SSQCore:
    """核心数据处理类 - 保持纯净，无UI依赖"""

    @staticmethod
    def load_cached_data():
        """加载缓存数据"""
        if not os.path.exists(AppConfig.CACHE_FILE):
            return None
        try:
            with open(AppConfig.CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            cache_time = datetime.fromisoformat(cache['timestamp'])
            if datetime.now() - cache_time < timedelta(days=AppConfig.CACHE_EXPIRY_DAYS):
                logging.info(f"缓存有效: {len(cache['data'])}条")
                return cache['data']
        except Exception as e:
            logging.error(f"加载缓存失败: {e}")
        return None

    @staticmethod
    def save_data_to_cache(data):
        """保存数据到缓存"""
        try:
            simplified_data = [
                {'red': item.get('red', ''), 'blue': item.get('blue', '')}
                for item in data
            ]
            cache = {
                'timestamp': datetime.now().isoformat(),
                'version': AppConfig.VERSION,
                'data': simplified_data,
                'raw_data': data
            }
            with open(AppConfig.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

            count = len(simplified_data)
            logging.info(f"缓存成功: {count}条")
            return True, f"缓存成功: {count}条"
        except Exception as e:
            logging.error(f"缓存失败: {e}")
            return False, f"缓存失败: {e}"

    @staticmethod
    def fetch_history_data():
        """从API获取历史数据"""
        try:
            response = requests.get(
                AppConfig.API_URL,
                timeout=AppConfig.TIMEOUT)
            response.raise_for_status()
            result = response.json()

            if result.get('state') != 0 or not result.get('result'):
                logging.error("API返回异常")
                return None, "API返回异常"

            data = result['result']
            logging.info(f"获取成功: {len(data)}条")
            return data, f"获取成功: {len(data)}条"
        except Exception as e:
            logging.error(f"获取失败: {e}")
            return None, f"获取失败: {str(e)}"

    @staticmethod
    def parse_numbers(data):
        """解析号码"""
        red_balls, blue_balls = [], []
        for item in data:
            if 'red' in item and item['red']:
                try:
                    nums = [int(x) for x in item['red'].split(',')]
                    if len(nums) == 6:
                        red_balls.extend(nums)
                except BaseException:
                    pass
            if 'blue' in item and item['blue']:
                try:
                    blue_balls.append(int(item['blue']))
                except BaseException:
                    pass
        return red_balls, blue_balls

    @staticmethod
    def analyze_frequency(red_balls, blue_balls):
        """分析频率"""
        red_freq = defaultdict(int)
        blue_freq = defaultdict(int)
        for num in red_balls:
            if 1 <= num <= 33:
                red_freq[num] += 1
        for num in blue_balls:
            if 1 <= num <= 16:
                blue_freq[num] += 1
        return red_freq, blue_freq

# ==================== 推荐算法引擎 ====================


class RecommendEngine:
    """推荐算法引擎 - 支持多种算法"""

    @staticmethod
    def generate(algorithm, red_freq, blue_freq, count=5):
        """根据算法生成推荐"""
        if algorithm == RecommendAlgorithm.FREQUENCY_WEIGHTED:
            return RecommendEngine._frequency_weighted(
                red_freq, blue_freq, count)
        elif algorithm == RecommendAlgorithm.PURE_RANDOM:
            return RecommendEngine._pure_random(count)
        elif algorithm == RecommendAlgorithm.PURE_FREQUENCY:
            return RecommendEngine._pure_frequency(red_freq, blue_freq, count)
        elif algorithm == RecommendAlgorithm.HOT_COLD_BALANCE:
            return RecommendEngine._hot_cold_balance(
                red_freq, blue_freq, count)
        elif algorithm == RecommendAlgorithm.INTERVAL_DISTRIBUTION:
            return RecommendEngine._interval_distribution(
                red_freq, blue_freq, count)
        elif algorithm == RecommendAlgorithm.ODD_EVEN_BALANCE:
            return RecommendEngine._odd_even_balance(
                red_freq, blue_freq, count)
        elif algorithm == RecommendAlgorithm.SUM_OPTIMIZED:
            return RecommendEngine._sum_optimized(red_freq, blue_freq, count)
        elif algorithm == RecommendAlgorithm.NO_CONSECUTIVE:
            return RecommendEngine._no_consecutive(red_freq, blue_freq, count)
        else:
            return RecommendEngine._frequency_weighted(
                red_freq, blue_freq, count)

    @staticmethod
    def _frequency_weighted(red_freq, blue_freq, count):
        """算法1：频率加权+随机（当前算法）"""
        recommendations = []
        all_reds = list(range(1, 34))
        all_blues = list(range(1, 17))

        for _ in range(count):
            red_weights = [
                red_freq.get(num, 1) + random.uniform(0.1, 1.0)
                for num in all_reds
            ]
            selected_reds = sorted(
                list(set(random.choices(all_reds, weights=red_weights, k=6)))[:6]
            )
            while len(selected_reds) < 6:
                candidate = random.randint(1, 33)
                if candidate not in selected_reds:
                    selected_reds.append(candidate)
                    selected_reds = sorted(selected_reds[:6])

            blue_weights = [blue_freq.get(num, 1) for num in all_blues]
            selected_blue = random.choices(
                all_blues, weights=blue_weights, k=1)[0]

            recommendations.append(
                {'red': selected_reds, 'blue': selected_blue})
        return recommendations

    @staticmethod
    def _pure_random(count):
        """算法2：纯随机"""
        recommendations = []
        for _ in range(count):
            reds = sorted(random.sample(range(1, 34), 6))
            blue = random.randint(1, 16)
            recommendations.append({'red': reds, 'blue': blue})
        return recommendations

    @staticmethod
    def _pure_frequency(red_freq, blue_freq, count):
        """算法3：纯频率（无随机）"""
        recommendations = []
        # 取最热门的6个红球
        top_reds = [
            num for num,
            _ in sorted(
                red_freq.items(),
                key=lambda x: x[1],
                reverse=True)[
                :6]]
        top_reds = sorted(top_reds)

        # 取最热门的1个蓝球
        top_blue = sorted(
            blue_freq.items(),
            key=lambda x: x[1],
            reverse=True)[0][0]

        for _ in range(count):
            recommendations.append({'red': top_reds, 'blue': top_blue})
        return recommendations

    @staticmethod
    def _hot_cold_balance(red_freq, blue_freq, count):
        """算法4：冷热平衡（3热3冷）"""
        recommendations = []
        all_reds = list(range(1, 34))
        all_blues = list(range(1, 17))

        for _ in range(count):
            # 红球：前10热门 + 后10冷门
            hot = sorted(
                red_freq.items(),
                key=lambda x: x[1],
                reverse=True)[
                :10]
            cold = sorted(red_freq.items(), key=lambda x: x[1])[:10]

            # 随机选3个热门 + 3个冷门
            hot_selected = random.sample([x[0] for x in hot], 3)
            cold_selected = random.sample([x[0] for x in cold], 3)
            selected_reds = sorted(hot_selected + cold_selected)

            # 蓝球：冷热各1个
            hot_blue = sorted(
                blue_freq.items(),
                key=lambda x: x[1],
                reverse=True)[0][0]
            cold_blue = sorted(blue_freq.items(), key=lambda x: x[1])[0][0]
            selected_blue = random.choice([hot_blue, cold_blue])

            recommendations.append(
                {'red': selected_reds, 'blue': selected_blue})
        return recommendations

    @staticmethod
    def _interval_distribution(red_freq, blue_freq, count):
        """算法5：区间分布（确保覆盖不同区间）"""
        recommendations = []

        for _ in range(count):
            # 红球区间：1-11, 12-22, 23-33
            interval1 = random.sample(range(1, 12), 2)
            interval2 = random.sample(range(12, 23), 2)
            interval3 = random.sample(range(23, 34), 2)
            selected_reds = sorted(interval1 + interval2 + interval3)

            # 蓝球区间：1-8, 9-16
            selected_blue = random.choice(
                [random.randint(1, 8), random.randint(9, 16)])

            recommendations.append(
                {'red': selected_reds, 'blue': selected_blue})
        return recommendations

    @staticmethod
    def _odd_even_balance(red_freq, blue_freq, count):
        """算法6：奇偶平衡（3奇3偶）"""
        recommendations = []

        for _ in range(count):
            # 红球：3奇数 + 3偶数
            odds = random.sample([x for x in range(1, 34) if x % 2 == 1], 3)
            evens = random.sample([x for x in range(1, 34) if x % 2 == 0], 3)
            selected_reds = sorted(odds + evens)

            # 蓝球：奇偶随机
            selected_blue = random.choice([random.randint(1, 16)])
            recommendations.append(
                {'red': selected_reds, 'blue': selected_blue})
        return recommendations

    @staticmethod
    def _sum_optimized(red_freq, blue_freq, count):
        """算法7：和值优化（红球和值在80-140之间）"""
        recommendations = []

        for _ in range(count):
            while True:
                selected_reds = sorted(random.sample(range(1, 34), 6))
                sum_value = sum(selected_reds)
                if 80 <= sum_value <= 140:  # 常见和值范围
                    break

            selected_blue = random.randint(1, 16)
            recommendations.append(
                {'red': selected_reds, 'blue': selected_blue})
        return recommendations

    @staticmethod
    def _no_consecutive(red_freq, blue_freq, count):
        """算法8：避免连号（任意两个号码不相邻）"""
        recommendations = []

        for _ in range(count):
            while True:
                selected_reds = sorted(random.sample(range(1, 34), 6))
                # 检查是否有连号
                has_consecutive = False
                for i in range(len(selected_reds) - 1):
                    if selected_reds[i + 1] - selected_reds[i] == 1:
                        has_consecutive = True
                        break
                if not has_consecutive:
                    break

            selected_blue = random.randint(1, 16)
            recommendations.append(
                {'red': selected_reds, 'blue': selected_blue})
        return recommendations

# ==================== 通信模块 ====================

class MessageQueue:
    """线程通信队列管理器"""

    def __init__(self):
        self.queue = queue.Queue()

    def send(self, msg_type: MessageType, data=None):
        """发送消息"""
        self.queue.put((msg_type, data))
        logging.debug(f"发送消息: {msg_type.value}")

    def receive(self):
        """接收消息（非阻塞）"""
        try:
            msg_type, data = self.queue.get_nowait()
            return msg_type, data
        except queue.Empty:
            return None, None

    def clear(self):
        """清空队列"""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except BaseException:
                break
        logging.info("队列已清空")

# ==================== GUI界面模块 ====================


def get_algorithm_description(algorithm):
    """获取算法说明"""
    descriptions = {
        RecommendAlgorithm.FREQUENCY_WEIGHTED:
            "频率加权+随机：基于历史频率，加入随机扰动，平衡热门和随机性",
        RecommendAlgorithm.PURE_RANDOM:
            "纯随机：完全随机生成，无任何历史数据依赖",
        RecommendAlgorithm.PURE_FREQUENCY:
            "纯频率：只选择历史最热门的号码，无随机性",
        RecommendAlgorithm.HOT_COLD_BALANCE:
            "冷热平衡：3个热门号码 + 3个冷门号码，平衡趋势",
        RecommendAlgorithm.INTERVAL_DISTRIBUTION:
            "区间分布：确保号码分布在1-11, 12-22, 23-33三个区间",
        RecommendAlgorithm.ODD_EVEN_BALANCE:
            "奇偶平衡：3个奇数 + 3个偶数，保持奇偶比例",
        RecommendAlgorithm.SUM_OPTIMIZED:
            "和值优化：红球和值控制在80-140之间（常见范围）",
        RecommendAlgorithm.NO_CONSECUTIVE:
            "避免连号：任意两个号码不相邻，减少连号概率"
    }
    return descriptions.get(algorithm, "")


class SSQGUI:
    """图形界面类 - 负责UI展示和用户交互"""

    def __init__(self, root):

        self.visualizer = None
        self.result_viz_frame = None
        self.freq_viz_frame = None
        self.result_text = None
        self.algo_desc_text = None
        self.algo_combo = None
        self.algorithm_var = None
        self.status_var = None
        self.cache_var = None

        self.root = root
        self.root.title(f"双色球智能推荐工具 v{AppConfig.VERSION}")
        self.root.geometry(AppConfig.WINDOW_SIZE)
        self.root.resizable(True, True)

        # 初始化通信队列
        self.message_queue = MessageQueue()

        # 绑定安全关闭
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 构建UI
        self.setup_ui()
        self.check_cache_status()

        # 启动消息处理
        self.process_messages()
        logging.info("GUI初始化完成")

    def setup_ui(self):
        """构建UI布局"""
        # 主容器
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧面板（操作 + 统计）
        left_panel = ttk.Frame(main_container)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        # 右侧面板（标签页）
        right_panel = ttk.Frame(main_container)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # 配置权重
        main_container.columnconfigure(0, weight=1)
        main_container.columnconfigure(1, weight=2)

        # ========== 左侧面板 ==========
        # ...左侧面板代码不变（状态、最新一期、按钮、进度条、统计）...

        # ========== 右侧面板（标签页）==========
        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # 标签1：推荐号码
        frame_recommend = ttk.Frame(notebook)
        notebook.add(frame_recommend, text="推荐号码")

        # 算法选择
        algo_frame = ttk.Frame(frame_recommend)
        algo_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(
            algo_frame,
            text="推荐算法:",
            font=(
                AppConfig.FONT_FAMILY,
                9)).pack(
            side=tk.LEFT)
        self.algorithm_var = tk.StringVar(
            value=RecommendAlgorithm.FREQUENCY_WEIGHTED.description)
        algo_options = [algo.description for algo in RecommendAlgorithm]
        self.algo_combo = ttk.Combobox(
            algo_frame,
            textvariable=self.algorithm_var,
            values=algo_options,
            state="readonly",
            width=20)
        self.algo_combo.pack(side=tk.LEFT, padx=5)
        self.algo_combo.bind("<<ComboboxSelected>>", self.on_algorithm_change)

        # 算法说明
        self.algo_desc_text = tk.Text(
            frame_recommend,
            height=2,
            font=(
                AppConfig.FONT_FAMILY,
                8),
            wrap=tk.WORD,
            relief=tk.FLAT,
            background="#F0F0F0")
        self.algo_desc_text.pack(fill=tk.X, pady=(0, 5))
        self.algo_desc_text.insert(
            tk.END, get_algorithm_description(
                RecommendAlgorithm.FREQUENCY_WEIGHTED))
        self.algo_desc_text.config(state=tk.DISABLED)

        # 推荐结果
        self.result_text = scrolledtext.ScrolledText(
            frame_recommend, height=8, font=(
                AppConfig.FONT_FAMILY_MONO, 12), wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.result_text.insert(tk.END, "点击【生成推荐】获取号码...")
        self.result_text.config(state=tk.DISABLED)

        # 标签2：频率可视化
        frame_freq_viz = ttk.Frame(notebook)
        notebook.add(frame_freq_viz, text="频率图表")

        self.freq_viz_frame = ttk.Frame(frame_freq_viz)
        self.freq_viz_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        freq_btn_frame = ttk.Frame(frame_freq_viz)
        freq_btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(
            freq_btn_frame,
            text="📊 频率分布",
            command=self.show_frequency_chart).pack(
            side=tk.LEFT,
            padx=5)
        ttk.Button(
            freq_btn_frame,
            text="🥧 热门占比",
            command=self.show_pie_chart).pack(
            side=tk.LEFT,
            padx=5)
        ttk.Button(
            freq_btn_frame,
            text="🗑 清除",
            command=self.clear_viz).pack(
            side=tk.LEFT,
            padx=5)

        # 标签3：推荐结果可视化
        frame_result_viz = ttk.Frame(notebook)
        notebook.add(frame_result_viz, text="推荐图表")

        self.result_viz_frame = ttk.Frame(frame_result_viz)
        self.result_viz_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        result_btn_frame = ttk.Frame(frame_result_viz)
        result_btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(
            result_btn_frame,
            text="📍 推荐分布",
            command=self.show_recommendation_chart).pack(
            side=tk.LEFT,
            padx=5)
        ttk.Button(
            result_btn_frame,
            text="🗑 清除",
            command=self.clear_viz).pack(
            side=tk.LEFT,
            padx=5)

        # 初始化可视化器
        self.visualizer = DataVisualizer(self.root)

        # ========== 底部声明 ==========
        footer = ttk.Label(main_container,
                           text="免责声明：彩票为随机事件，本工具仅供娱乐参考，不保证中奖",
                           font=(AppConfig.FONT_FAMILY, 7),
                           foreground="gray")
        footer.grid(row=1, column=0, columnspan=2, pady=(5, 0))

    # 添加可视化方法
    def show_frequency_chart(self):
        """显示频率分布图"""
        data = SSQCore.load_cached_data()
        if not data:
            messagebox.showwarning("警告", "没有缓存数据！")
            return

        try:
            red_balls, blue_balls = SSQCore.parse_numbers(data)
            red_freq, blue_freq = SSQCore.analyze_frequency(
                red_balls, blue_balls)
            self.visualizer.create_frequency_chart(
                red_freq, blue_freq, self.freq_viz_frame)
            logging.info("显示频率分布图")
        except Exception as e:
            messagebox.showerror("错误", f"图表生成失败: {e}")

    def show_pie_chart(self):
        """显示饼图"""
        data = SSQCore.load_cached_data()
        if not data:
            messagebox.showwarning("警告", "没有缓存数据！")
            return

        try:
            red_balls, blue_balls = SSQCore.parse_numbers(data)
            red_freq, blue_freq = SSQCore.analyze_frequency(
                red_balls, blue_balls)
            self.visualizer.create_pie_chart(
                red_freq, blue_freq, self.freq_viz_frame)
            logging.info("显示饼图")
        except Exception as e:
            messagebox.showerror("错误", f"图表生成失败: {e}")

    def show_recommendation_chart(self):
        """显示推荐结果分布图"""
        result = self.result_text.get(1.0, tk.END).strip()
        if "第1组" not in result:
            messagebox.showwarning("警告", "请先生成推荐！")
            return

        try:
            recommendations = []
            lines = result.split('\n')
            for line in lines:
                if "第" in line and "组:" in line:
                    parts = line.split('[')
                    if len(parts) >= 3:
                        red_str = parts[1].replace(']', '').strip()
                        blue_str = parts[2].replace(']', '').strip()
                        reds = [int(x) for x in red_str.split()]
                        blue = int(blue_str)
                        recommendations.append({'red': reds, 'blue': blue})

            if recommendations:
                self.visualizer.create_recommendation_chart(
                    recommendations, self.result_viz_frame)
                logging.info("显示推荐分布图")
            else:
                messagebox.showerror("错误", "无法解析推荐结果")
        except Exception as e:
            messagebox.showerror("错误", f"图表生成失败: {e}")

    def clear_viz(self):
        """清除图表"""
        self.visualizer.clear()
        logging.info("清除图表")

    def on_algorithm_change(self, event):
        """算法选择变化时更新说明"""
        selected_desc = self.algorithm_var.get()
        # 找到对应的算法枚举
        for algo in RecommendAlgorithm:
            if algo.description == selected_desc:
                description = get_algorithm_description(algo)
                self.algo_desc_text.config(state=tk.NORMAL)
                self.algo_desc_text.delete(1.0, tk.END)
                self.algo_desc_text.insert(tk.END, description)
                self.algo_desc_text.config(state=tk.DISABLED)
                break

    def check_cache_status(self):
        """检查缓存状态"""
        data = SSQCore.load_cached_data()
        if data:
            self.cache_var.set(f"缓存: {len(data)}条（有效）")
            self.status_var.set("缓存可用，可直接生成推荐")
        else:
            self.cache_var.set("缓存: 无/过期")
            self.status_var.set("请先获取数据")

    def clear_cache(self):
        """清除缓存"""
        if not os.path.exists(AppConfig.CACHE_FILE):
            messagebox.showinfo("提示", "没有找到缓存文件")
            return

        if not messagebox.askyesno("确认", "确定要清除缓存数据吗？"):
            return

        try:
            os.remove(AppConfig.CACHE_FILE)

            # 更新状态显示
            self.cache_var.set("缓存: 无/过期")
            self.status_var.set("缓存已清除，请重新获取")

            # 清空所有统计文本框
            for text_widget in [self.hot_red_text, self.cold_red_text,
                                self.hot_blue_text, self.cold_blue_text]:
                text_widget.config(state=tk.NORMAL)
                text_widget.delete(1.0, tk.END)
                text_widget.insert(tk.END, "请先获取数据...")
                text_widget.config(state=tk.DISABLED)

            # 清空推荐结果
            self.result_text.config(state=tk.NORMAL)
            self.result_text.delete(1.0, tk.END)
            self.result_text.insert(tk.END, "点击【生成推荐】获取号码...")
            self.result_text.config(state=tk.DISABLED)

            # 清空最新一期展示
            self.latest_result_text.config(state=tk.NORMAL)
            self.latest_result_text.delete(1.0, tk.END)
            self.latest_result_text.insert(tk.END, "请先获取数据...")
            self.latest_result_text.config(state=tk.DISABLED)

            # 清空球形展示
            for widget in self.ball_frame.winfo_children():
                widget.destroy()

            messagebox.showinfo("成功", "缓存已清除！")
            logging.info("缓存清除成功")

        except Exception as e:
            messagebox.showerror("错误", f"清除失败: {e}")
            logging.error(f"清除缓存失败: {e}")

    def start_fetch_data(self):
        """启动数据获取"""
        if self.btn_fetch.cget('state') == 'disabled':
            logging.warning("重复点击被忽略")
            return

        self._set_ui_busy(True)
        self.message_queue.clear()
        self.message_queue.send(MessageType.PROGRESS_START)

        thread = threading.Thread(target=self._fetch_data_worker, daemon=True)
        thread.start()
        logging.info("数据获取线程已启动")

    def _fetch_data_worker(self):
        """数据获取工作线程"""
        try:
            data, msg = SSQCore.fetch_history_data()
            if data:
                success, cache_msg = SSQCore.save_data_to_cache(data)
                self.message_queue.send(
                    MessageType.FETCH_SUCCESS,
                    f"{msg}\n{cache_msg}")
            else:
                self.message_queue.send(MessageType.FETCH_ERROR, msg)
        except Exception as e:
            self.message_queue.send(MessageType.FETCH_ERROR, f"线程异常: {str(e)}")

    def start_generate_recommend(self):
        """启动推荐生成"""
        data = SSQCore.load_cached_data()
        if not data:
            messagebox.showwarning("警告", "没有有效的缓存数据！")
            return

        # 获取选中的算法
        selected_desc = self.algorithm_var.get()
        selected_algorithm = None
        for algo in RecommendAlgorithm:
            if algo.description == selected_desc:
                selected_algorithm = algo
                break

        if not selected_algorithm:
            messagebox.showerror("错误", "请选择有效的推荐算法！")
            return

        self._set_ui_busy(True)
        self.message_queue.send(MessageType.PROGRESS_START)

        # 传递算法参数
        thread = threading.Thread(
            target=self._generate_recommend_worker,
            args=(selected_algorithm,),
            daemon=True)
        thread.start()
        logging.info(f"推荐生成线程已启动，算法: {selected_algorithm.key}")

    def _generate_recommend_worker(self, algorithm):
        """推荐生成工作线程"""
        try:
            data = SSQCore.load_cached_data()
            red_balls, blue_balls = SSQCore.parse_numbers(data)
            red_freq, blue_freq = SSQCore.analyze_frequency(
                red_balls, blue_balls)

            # 使用算法引擎生成推荐
            recommendations = RecommendEngine.generate(
                algorithm, red_freq, blue_freq)

            # 准备推荐结果
            result_lines = [
                f"📊 算法: {algorithm.description}",
                f"📊 分析基数: {len(red_balls)}个红球, {len(blue_balls)}个蓝球",
                "=" * 40
            ]

            for i, rec in enumerate(recommendations, 1):
                red_str = " ".join(f"{num:02d}" for num in rec['red'])
                result_lines.append(
                    f"第{i}组: 红球 [{red_str}]  蓝球 [{rec['blue']:02d}]")

            result_lines.append("\n" + "=" * 40)
            result_lines.append("💡 提示：多次运行获取不同组合")

            self.message_queue.send(
                MessageType.RECOMMEND_SUCCESS,
                "\n".join(result_lines))

        except Exception as e:
            self.message_queue.send(MessageType.ERROR, f"生成失败: {str(e)}")

    def process_messages(self):
        """处理消息队列（UI更新）"""
        msg_type, content = self.message_queue.receive()

        if msg_type:
            logging.info(f"处理消息: {msg_type.value}")

            try:
                if msg_type == MessageType.FETCH_SUCCESS:
                    # 解析消息内容
                    lines = content.split('\n')
                    status_msg = lines[0]
                    cache_msg = lines[1] if len(lines) > 1 else ""

                    self.status_var.set(status_msg)

                    # 修复冒号问题
                    try:
                        with open(AppConfig.CACHE_FILE, 'r', encoding='utf-8') as f:
                            cache = json.load(f)
                        data_count = len(cache.get('data', []))
                        self.cache_var.set(f"缓存: {data_count}条（有效）")
                    except BaseException:
                        if "缓存成功" in cache_msg:
                            count = cache_msg.replace(
                                "缓存成功:", "").replace(
                                "条", "").strip()
                            self.cache_var.set(f"缓存: {count}条（有效）")
                        else:
                            self.cache_var.set("缓存: 有效")

                    # 展示最新一期
                    try:
                        with open(AppConfig.CACHE_FILE, 'r', encoding='utf-8') as f:
                            cache = json.load(f)
                        raw_data = cache.get('raw_data', [])
                        if raw_data:
                            self.show_latest_result(raw_data)
                    except BaseException:
                        data = SSQCore.load_cached_data()
                        if data:
                            self.show_latest_result(data)

                    # 展示历史统计（4个区域，每区域14个球，7行）
                    try:
                        data = SSQCore.load_cached_data()
                        if data:
                            red_balls, blue_balls = SSQCore.parse_numbers(data)
                            red_freq, blue_freq = SSQCore.analyze_frequency(
                                red_balls, blue_balls)

                            # 1. 热门红球（前14个，配对成7行）
                            top_reds = sorted(
                                red_freq.items(),
                                key=lambda x: x[1],
                                reverse=True)[
                                :14]
                            hot_red_lines = []
                            for i in range(0, 14, 2):
                                num1, freq1 = top_reds[i]
                                num2, freq2 = top_reds[i + 1]
                                hot_red_lines.append(
                                    f"{num1:02d}:{freq1:3d}次      {num2:02d}:{freq2:3d}次")

                            # 2. 冷门红球（后14个，配对成7行）
                            bottom_reds = sorted(
                                red_freq.items(), key=lambda x: x[1])[:14]
                            cold_red_lines = []
                            for i in range(0, 14, 2):
                                num1, freq1 = bottom_reds[i]
                                num2, freq2 = bottom_reds[i + 1]
                                cold_red_lines.append(
                                    f"{num1:02d}:{freq1:3d}次      {num2:02d}:{freq2:3d}次")

                            # 3. 热门蓝球（前14个，配对成7行）
                            top_blues = sorted(
                                blue_freq.items(),
                                key=lambda x: x[1],
                                reverse=True)[
                                :14]
                            hot_blue_lines = []
                            for i in range(0, 14, 2):
                                num1, freq1 = top_blues[i]
                                num2, freq2 = top_blues[i + 1]
                                hot_blue_lines.append(
                                    f"{num1:02d}:{freq1:3d}次      {num2:02d}:{freq2:3d}次")

                            # 4. 冷门蓝球（后14个，配对成7行）
                            bottom_blues = sorted(
                                blue_freq.items(), key=lambda x: x[1])[:14]
                            cold_blue_lines = []
                            for i in range(0, 14, 2):
                                num1, freq1 = bottom_blues[i]
                                num2, freq2 = bottom_blues[i + 1]
                                cold_blue_lines.append(
                                    f"{num1:02d}:{freq1:3d}次      {num2:02d}:{freq2:3d}次")

                            # 更新四个区域
                            self.hot_red_text.config(state=tk.NORMAL)
                            self.hot_red_text.delete(1.0, tk.END)
                            self.hot_red_text.insert(
                                tk.END, "\n".join(hot_red_lines))
                            self.hot_red_text.config(state=tk.DISABLED)

                            self.cold_red_text.config(state=tk.NORMAL)
                            self.cold_red_text.delete(1.0, tk.END)
                            self.cold_red_text.insert(
                                tk.END, "\n".join(cold_red_lines))
                            self.cold_red_text.config(state=tk.DISABLED)

                            self.hot_blue_text.config(state=tk.NORMAL)
                            self.hot_blue_text.delete(1.0, tk.END)
                            self.hot_blue_text.insert(
                                tk.END, "\n".join(hot_blue_lines))
                            self.hot_blue_text.config(state=tk.DISABLED)

                            self.cold_blue_text.config(state=tk.NORMAL)
                            self.cold_blue_text.delete(1.0, tk.END)
                            self.cold_blue_text.insert(
                                tk.END, "\n".join(cold_blue_lines))
                            self.cold_blue_text.config(state=tk.DISABLED)

                            logging.info("历史统计已展示（4个区域，每区域14个球）")
                    except Exception as e:
                        logging.error(f"展示历史统计失败: {e}")

                    self._set_ui_busy(False)
                    messagebox.showinfo("成功", content)
                    logging.info("UI更新完成 - 获取数据成功")

                elif msg_type == MessageType.FETCH_ERROR:
                    self.status_var.set("获取失败")
                    self._set_ui_busy(False)
                    messagebox.showerror("错误", content)

                elif msg_type == MessageType.RECOMMEND_SUCCESS:
                    self.result_text.config(state=tk.NORMAL)
                    self.result_text.delete(1.0, tk.END)
                    self.result_text.insert(tk.END, content)
                    self.result_text.config(state=tk.DISABLED)
                    self.status_var.set("推荐生成完成")
                    self._set_ui_busy(False)

                elif msg_type == MessageType.ERROR:
                    self.status_var.set("发生错误")
                    self._set_ui_busy(False)
                    messagebox.showerror("错误", content)

                elif msg_type == MessageType.PROGRESS_START:
                    self.progress.start(10)

                elif msg_type == MessageType.PROGRESS_STOP:
                    self.progress.stop()

            except Exception as e:
                logging.error(f"消息处理异常: {e}")
                import traceback
                traceback.print_exc()
                self._set_ui_busy(False)

        # 继续监听
        self.root.after(100, self.process_messages)

    def _set_ui_busy(self, busy: bool):
        """设置UI忙碌状态"""
        state = tk.DISABLED if busy else tk.NORMAL
        self.btn_fetch.config(state=state)
        self.btn_recommend.config(state=state)
        self.algo_combo.config(state=state)

        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()

    def show_latest_result(self, data):
        """展示最新一期开奖结果（文本+球形）"""
        if not data or len(data) == 0:
            return

        latest = data[0]
        try:
            issue = latest.get('code', '未知期号')
            date = latest.get('date', '未知日期')

            if isinstance(date, dict):
                date = date.get('date', '未知日期')
        except BaseException:
            issue = '未知期号'
            date = '未知日期'

        reds = latest.get('red', '')
        blues = latest.get('blue', '')

        # 解析号码
        red_list = [int(x) for x in reds.split(',')] if reds else []
        blue_list = [int(blues)] if blues else []

        # 更新文本展示 - 左对齐
        self.latest_result_text.config(state=tk.NORMAL)
        self.latest_result_text.delete(1.0, tk.END)
        self.latest_result_text.insert(tk.END, f"期号: {issue}  日期: {date}")
        self.latest_result_text.config(state=tk.DISABLED)

        # 绘制球形 - 左对齐
        self.draw_balls(red_list, blue_list)

    def draw_balls(self, red_list, blue_list):
        """绘制彩色球体"""
        # 清空旧球体
        for widget in self.ball_frame.winfo_children():
            widget.destroy()

        # 创建红球（红色背景，白色文字，圆形按钮样式）
        for num in red_list:
            ball = tk.Label(
                self.ball_frame,
                text=f"{num:02d}",
                font=(AppConfig.FONT_FAMILY, 10, "bold"),
                bg="red",
                fg="white",
                width=3,
                height=1,
                relief="raised",
                bd=2)
            ball.pack(side=tk.LEFT, padx=2)

        # 分隔符
        ttk.Label(self.ball_frame, text="  |  ").pack(side=tk.LEFT)

        # 创建蓝球（蓝色背景，白色文字，圆形按钮样式）
        for num in blue_list:
            ball = tk.Label(
                self.ball_frame,
                text=f"{num:02d}",
                font=(AppConfig.FONT_FAMILY, 10, "bold"),
                bg="blue",
                fg="white",
                width=3,
                height=1,
                relief="raised",
                bd=2)
            ball.pack(side=tk.LEFT, padx=2)

    def on_closing(self):
        """安全关闭"""
        logging.info("程序关闭中...")
        self.message_queue.clear()
        self.progress.stop()
        try:
            self.btn_fetch.config(state=tk.NORMAL)
            self.btn_recommend.config(state=tk.NORMAL)
        except BaseException:
            pass
        self.root.destroy()

# ==================== 程序入口 ====================


class DataVisualizer:
    """数据可视化器"""

    def __init__(self, parent):
        self.parent = parent
        self.figure = None
        self.canvas = None

    def create_frequency_chart(self, red_freq, blue_freq, parent_frame, red_nums=None):
        """创建频率分布图"""
        # 清除旧图表
        for widget in parent_frame.winfo_children():
            widget.destroy()

        # 创建图形
        self.figure, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
        self.figure.tight_layout(pad=3.0)

        # 红球频率图        red_nums = list(range(1, 34))
        red_counts = [red_freq.get(num, 0) for num in red_nums]
        ax1.bar(red_nums, red_counts, color='red', alpha=0.7)
        ax1.set_title('红球频率分布', fontproperties="Microsoft YaHei", fontsize=12)
        ax1.set_xlabel('号码', fontproperties="Microsoft YaHei")
        ax1.set_ylabel('出现次数', fontproperties="Microsoft YaHei")
        ax1.set_xticks(range(1, 34, 3))

        # 蓝球频率图
        blue_nums = list(range(1, 17))
        blue_counts = [blue_freq.get(num, 0) for num in blue_nums]
        ax2.bar(blue_nums, blue_counts, color='blue', alpha=0.7)
        ax2.set_title('蓝球频率分布', fontproperties="Microsoft YaHei", fontsize=12)
        ax2.set_xlabel('号码', fontproperties="Microsoft YaHei")
        ax2.set_ylabel('出现次数', fontproperties="Microsoft YaHei")
        ax2.set_xticks(range(1, 17, 2))

        # 嵌入到Tkinter
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def create_recommendation_chart(self, recommendations, parent_frame):
        """创建推荐结果可视化"""
        # 清除旧图表
        for widget in parent_frame.winfo_children():
            widget.destroy()

        # 创建图形
        self.figure, ax = plt.subplots(figsize=(10, 5))
        self.figure.tight_layout(pad=2.0)

        # 准备数据
        group_labels = []
        red_positions = []
        blue_positions = []

        for i, rec in enumerate(recommendations, 1):
            group_labels.append(f"第{i}组")
            for red in rec['red']:
                red_positions.append((i, red))
            blue_positions.append((i, rec['blue']))

        # 绘制红球
        if red_positions:
            groups, reds = zip(*red_positions)
            ax.scatter(
                groups,
                reds,
                color='red',
                s=100,
                alpha=0.6,
                label='红球',
                marker='o')

        # 绘制蓝球
        if blue_positions:
            groups, blues = zip(*blue_positions)
            ax.scatter(
                groups,
                blues,
                color='blue',
                s=150,
                alpha=0.8,
                label='蓝球',
                marker='s')

        # 设置标签
        ax.set_title('推荐号码分布图', fontproperties="Microsoft YaHei", fontsize=14)
        ax.set_xlabel('推荐组别', fontproperties="Microsoft YaHei")
        ax.set_ylabel('号码', fontproperties="Microsoft YaHei")
        ax.set_xticks(range(1, len(recommendations) + 1))
        ax.set_yticks(range(1, 34, 2))
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 嵌入到Tkinter
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def create_pie_chart(self, red_freq, blue_freq, parent_frame):
        """创建饼图（展示热门号码占比）"""
        # 清除旧图表
        for widget in parent_frame.winfo_children():
            widget.destroy()

        # 创建图形
        self.figure, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
        self.figure.tight_layout(pad=3.0)

        # 红球Top5饼图
        top_reds = sorted(
            red_freq.items(),
            key=lambda x: x[1],
            reverse=True)[
            :5]
        if top_reds:
            labels = [f"{num:02d}" for num, _ in top_reds]
            sizes = [count for _, count in top_reds]
            colors = ['#FF6B6B', '#FF8E8E', '#FFB3B3', '#FFD6D6', '#FFF0F0']
            ax1.pie(
                sizes,
                labels=labels,
                colors=colors,
                autopct='%1.1f%%',
                startangle=90)
            ax1.set_title(
                '红球TOP5占比',
                fontproperties="Microsoft YaHei",
                fontsize=12)

        # 蓝球Top5饼图
        top_blues = sorted(
            blue_freq.items(),
            key=lambda x: x[1],
            reverse=True)[
            :5]
        if top_blues:
            labels = [f"{num:02d}" for num, _ in top_blues]
            sizes = [count for _, count in top_blues]
            colors = ['#4D96FF', '#6DABE8', '#8DC0E8', '#ADCCE8', '#CDE0F8']
            ax2.pie(
                sizes,
                labels=labels,
                colors=colors,
                autopct='%1.1f%%',
                startangle=90)
            ax2.set_title(
                '蓝球TOP5占比',
                fontproperties="Microsoft YaHei",
                fontsize=12)

        # 嵌入到Tkinter
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def clear(self):
        """清除图表"""
        if self.canvas:
            self.canvas.get_tk_widget().destroy()
        if self.figure:
            plt.close(self.figure)


def main():
    """主入口"""
    setup_logging()
    logging.info(f"启动双色球推荐工具 v{AppConfig.VERSION}")

    root = tk.Tk()
    try:
        root.iconbitmap("app.ico")
    except BaseException:
        pass

    app = SSQGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
