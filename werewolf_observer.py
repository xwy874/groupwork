#!/usr/bin/env python3
"""
狼人杀智能体观察台 - 完整版（可靠的手动接管）
"""

import asyncio
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
from datetime import datetime
from typing import Dict, List, Optional
import time
import random

from werewolf_gateway import LLMGateway, GameSession, AgentResponse
from werewolf_gateway.config import GatewaySettings


DEFAULT_API_KEY = "sk-Jy5_QXGOOSkpR_NZelw4ww"
DEFAULT_BASE_URL = "https://models.sjtu.edu.cn/api/v1"

COLORS = {
    "bg_dark": "#1e1e2e",
    "bg_light": "#313244",
    "accent": "#89b4fa",
    "text": "#cdd6f4",
    "thought": "#a6e3a1",
    "success": "#a6e3a1",
    "warning": "#f38ba8",
}

ROLE_NAMES = {
    "werewolf": "🐺 狼人",
    "villager": "👨 村民",
    "seer": "🔮 预言家",
    "witch": "🧪 女巫",
    "hunter": "🏹 猎人",
    "guard": "🛡️ 守卫",
}

GAME_CONFIGS = {
    6: {"roles": ["werewolf", "werewolf", "villager", "villager", "villager", "seer"],
        "desc": "6人局：2狼 + 3村民 + 预言家"},
    9: {"roles": ["werewolf", "werewolf", "werewolf", "villager", "villager", "villager", "villager", "seer", "witch"],
        "desc": "9人局：3狼 + 4村民 + 预言家 + 女巫"},
    12: {"roles": ["werewolf", "werewolf", "werewolf", "werewolf", "villager", "villager", "villager", "villager", "seer", "witch", "hunter", "guard"],
         "desc": "12人局：4狼 + 4村民 + 预言家 + 女巫 + 猎人 + 守卫"}
}


class WerewolfObserverUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("🐺 狼人杀智能体观察台")
        self.root.geometry("1500x900")
        self.root.configure(bg=COLORS["bg_dark"])
        
        self.game_running = False
        self.game_paused = False
        self.game_thread: Optional[threading.Thread] = None
        self.thoughts_history: Dict[int, List[Dict]] = {}
        self.player_frames = []
        self.current_player_count = 6
        
        # 手动接管数据
        self.manual_speech: Dict[int, str] = {}  # {座位: 发言内容}
        self.manual_vote: Dict[int, int] = {}    # {座位: 投票目标}
        
        self.setup_ui()
        self.init_players_by_count(6)
        
    def setup_ui(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_frame = tk.Frame(main_frame, bg=COLORS["bg_dark"], width=350)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)
        
        right_frame = tk.Frame(main_frame, bg=COLORS["bg_dark"])
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.setup_left_panel(left_frame)
        self.setup_right_panel(right_frame)
        
    def setup_left_panel(self, parent):
        api_frame = tk.LabelFrame(parent, text="API配置", bg=COLORS["bg_dark"], fg=COLORS["accent"])
        api_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(api_frame, text="API Key:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=5)
        self.api_key_entry = tk.Entry(api_frame, bg=COLORS["bg_light"], fg=COLORS["text"])
        self.api_key_entry.insert(0, DEFAULT_API_KEY)
        self.api_key_entry.pack(fill=tk.X, padx=5, pady=2)
        
        tk.Label(api_frame, text="Base URL:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=5)
        self.base_url_entry = tk.Entry(api_frame, bg=COLORS["bg_light"], fg=COLORS["text"])
        self.base_url_entry.insert(0, DEFAULT_BASE_URL)
        self.base_url_entry.pack(fill=tk.X, padx=5, pady=2)
        
        rate_frame = tk.LabelFrame(parent, text="速率限制", bg=COLORS["bg_dark"], fg=COLORS["accent"])
        rate_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(rate_frame, text="请求间隔(秒):", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=5)
        self.delay_var = tk.StringVar(value="2")
        ttk.Spinbox(rate_frame, from_=1, to=5, textvariable=self.delay_var, width=8).pack(anchor=tk.W, padx=5, pady=2)
        
        test_btn = tk.Button(rate_frame, text="测试连接", command=self.test_connection,
                             bg=COLORS["accent"], fg=COLORS["bg_dark"])
        test_btn.pack(pady=5, padx=5, fill=tk.X)
        
        game_frame = tk.LabelFrame(parent, text="游戏配置", bg=COLORS["bg_dark"], fg=COLORS["accent"])
        game_frame.pack(fill=tk.X, padx=10, pady=5)
        
        count_frame = tk.Frame(game_frame, bg=COLORS["bg_dark"])
        count_frame.pack(fill=tk.X, padx=5, pady=5)
        
        tk.Label(count_frame, text="人数:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(side=tk.LEFT)
        self.count_var = tk.StringVar(value="6")
        ttk.Combobox(count_frame, textvariable=self.count_var, values=["6", "9", "12"],
                     state="readonly", width=8).pack(side=tk.LEFT, padx=5)
        self.count_var.trace("w", lambda *_: self.on_count_changed())
        
        self.desc_label = tk.Label(game_frame, text=GAME_CONFIGS[6]["desc"], bg=COLORS["bg_dark"],
                                   fg=COLORS["thought"], font=("Arial", 9))
        self.desc_label.pack(pady=5)
        
        players_frame = tk.LabelFrame(parent, text="角色配置", bg=COLORS["bg_dark"], fg=COLORS["accent"])
        players_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(players_frame, bg=COLORS["bg_dark"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(players_frame, orient="vertical", command=canvas.yview)
        self.players_container = tk.Frame(canvas, bg=COLORS["bg_dark"])
        
        self.players_container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.players_container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # 手动接管面板
        manual_frame = tk.LabelFrame(parent, text="手动接管", bg=COLORS["bg_dark"], fg=COLORS["accent"])
        manual_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # 玩家选择
        seat_frame = tk.Frame(manual_frame, bg=COLORS["bg_dark"])
        seat_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(seat_frame, text="玩家:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(side=tk.LEFT)
        self.manual_seat = ttk.Combobox(seat_frame, values=[str(i) for i in range(1, 13)], state="readonly", width=5)
        self.manual_seat.pack(side=tk.LEFT, padx=5)
        self.manual_seat.set("1")
        
        # 发言输入
        tk.Label(manual_frame, text="发言内容:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=5)
        self.speech_text = tk.Text(manual_frame, height=3, bg=COLORS["bg_light"], fg=COLORS["text"])
        self.speech_text.pack(fill=tk.X, padx=5, pady=2)
        
        # 提交按钮
        btn_frame = tk.Frame(manual_frame, bg=COLORS["bg_dark"])
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.submit_speech_btn = tk.Button(btn_frame, text="提交发言", command=self.submit_speech,
                                           bg=COLORS["success"], fg=COLORS["bg_dark"])
        self.submit_speech_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        self.submit_vote_btn = tk.Button(btn_frame, text="提交投票", command=self.submit_vote,
                                         bg=COLORS["warning"], fg=COLORS["bg_dark"])
        self.submit_vote_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        # 显示当前接管状态
        self.manual_status = tk.Label(manual_frame, text="", bg=COLORS["bg_dark"], fg=COLORS["thought"],
                                       font=("Arial", 9))
        self.manual_status.pack(anchor=tk.W, padx=5, pady=2)
        
        # 游戏控制按钮
        btn_frame2 = tk.Frame(parent, bg=COLORS["bg_dark"])
        btn_frame2.pack(fill=tk.X, padx=10, pady=10)
        
        self.start_btn = tk.Button(btn_frame2, text="开始游戏", command=self.start_game,
                                   bg=COLORS["success"], fg=COLORS["bg_dark"], font=("Arial", 12, "bold"))
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.pause_btn = tk.Button(btn_frame2, text="暂停", command=self.toggle_pause,
                                   bg=COLORS["accent"], fg=COLORS["bg_dark"], state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.stop_btn = tk.Button(btn_frame2, text="停止", command=self.stop_game,
                                  bg=COLORS["warning"], fg=COLORS["bg_dark"], state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.status_label = tk.Label(parent, text="⚪ 未开始", bg=COLORS["bg_dark"], fg=COLORS["text"])
        self.status_label.pack(pady=5)
        
    def setup_right_panel(self, parent):
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)
        
        chat_frame = tk.Frame(notebook, bg=COLORS["bg_dark"])
        notebook.add(chat_frame, text="对话记录")
        
        self.chat_display = scrolledtext.ScrolledText(chat_frame, bg=COLORS["bg_light"],
                                                       fg=COLORS["text"], font=("Arial", 11),
                                                       wrap=tk.WORD)
        self.chat_display.pack(fill=tk.BOTH, expand=True, pady=5)
        
        thought_frame = tk.Frame(notebook, bg=COLORS["bg_dark"])
        notebook.add(thought_frame, text="思维链")
        
        filter_frame = tk.Frame(thought_frame, bg=COLORS["bg_dark"])
        filter_frame.pack(fill=tk.X, pady=5)
        tk.Label(filter_frame, text="筛选:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(side=tk.LEFT, padx=5)
        self.filter_var = tk.StringVar(value="all")
        self.filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, values=["all"],
                                          state="readonly", width=15)
        self.filter_combo.pack(side=tk.LEFT, padx=5)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_thoughts())
        
        self.thought_display = scrolledtext.ScrolledText(thought_frame, bg=COLORS["bg_light"],
                                                          fg=COLORS["thought"], font=("Consolas", 10))
        self.thought_display.pack(fill=tk.BOTH, expand=True)
        
        log_frame = tk.Frame(notebook, bg=COLORS["bg_dark"])
        notebook.add(log_frame, text="日志")
        
        self.log_display = scrolledtext.ScrolledText(log_frame, bg=COLORS["bg_light"],
                                                      fg=COLORS["text"], font=("Consolas", 10))
        self.log_display.pack(fill=tk.BOTH, expand=True)
        
    def update_manual_status(self):
        """更新手动接管状态显示"""
        speech_info = f"发言:{list(self.manual_speech.keys())}" if self.manual_speech else ""
        vote_info = f"投票:{list(self.manual_vote.keys())}" if self.manual_vote else ""
        if speech_info or vote_info:
            self.manual_status.config(text=f"待处理: {speech_info} {vote_info}")
        else:
            self.manual_status.config(text="无待处理接管")
            
    def submit_speech(self):
        """提交手动发言"""
        try:
            seat = int(self.manual_seat.get())
            speech = self.speech_text.get("1.0", tk.END).strip()
            
            if not speech:
                messagebox.showwarning("警告", "请输入发言内容")
                return
                
            self.manual_speech[seat] = speech
            self.update_manual_status()
            self.log(f"已提交玩家{seat}的手动发言")
            self.add_chat_system(f"✋ 已接管玩家{seat}，下次发言将使用手动输入")
            self.speech_text.delete("1.0", tk.END)
            
        except Exception as e:
            self.log(f"提交失败: {e}")
            
    def submit_vote(self):
        """提交手动投票"""
        try:
            seat = int(self.manual_seat.get())
            
            # 弹出投票窗口
            vote_win = tk.Toplevel(self.root)
            vote_win.title(f"玩家{seat} 投票")
            vote_win.geometry("300x400")
            vote_win.configure(bg=COLORS["bg_dark"])
            vote_win.transient(self.root)
            vote_win.grab_set()
            
            tk.Label(vote_win, text=f"玩家{seat} 投票给:", 
                    bg=COLORS["bg_dark"], fg=COLORS["text"], font=("Arial", 12)).pack(pady=10)
            
            alive = [p['seat'] for p in self.player_frames if p['alive'] and p['seat'] != seat]
            vote_var = tk.StringVar()
            
            for s in alive:
                role = next((p['role_var'].get() for p in self.player_frames if p['seat'] == s), "未知")
                rb = tk.Radiobutton(vote_win, text=f"玩家{s} ({ROLE_NAMES.get(role, role)})",
                                    variable=vote_var, value=str(s),
                                    bg=COLORS["bg_dark"], fg=COLORS["text"],
                                    selectcolor=COLORS["bg_dark"])
                rb.pack(anchor=tk.W, padx=20, pady=5)
            
            def confirm():
                if vote_var.get():
                    target = int(vote_var.get())
                    self.manual_vote[seat] = target
                    self.update_manual_status()
                    self.log(f"已提交玩家{seat}的手动投票，目标玩家{target}")
                    self.add_chat_system(f"✋ 已接管玩家{seat}，下次投票将使用手动选择")
                    vote_win.destroy()
                else:
                    messagebox.showwarning("警告", "请选择投票目标")
            
            tk.Button(vote_win, text="确认", command=confirm,
                     bg=COLORS["success"], fg=COLORS["bg_dark"]).pack(pady=20)
            
        except Exception as e:
            self.log(f"提交投票失败: {e}")
        
    def test_connection(self):
        api_key = self.api_key_entry.get().strip()
        base_url = self.base_url_entry.get().strip()
        
        def test():
            try:
                import httpx
                client = httpx.Client(base_url=base_url, timeout=10)
                resp = client.get("/models", headers={"Authorization": f"Bearer {api_key}"})
                if resp.status_code == 200:
                    self.root.after(0, lambda: messagebox.showinfo("成功", "API连接正常！"))
                else:
                    self.root.after(0, lambda: messagebox.showerror("失败", f"HTTP {resp.status_code}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("失败", str(e)))
        
        threading.Thread(target=test, daemon=True).start()
        
    def on_count_changed(self):
        new_count = int(self.count_var.get())
        if new_count != self.current_player_count:
            self.current_player_count = new_count
            self.desc_label.config(text=GAME_CONFIGS[new_count]["desc"])
            self.init_players_by_count(new_count)
            self.manual_seat['values'] = [str(i) for i in range(1, new_count + 1)]
            self.manual_seat.set("1")
            
    def init_players_by_count(self, count: int):
        for widget in self.players_container.winfo_children():
            widget.destroy()
        
        self.player_frames = []
        config = GAME_CONFIGS[count]
        
        for i, role in enumerate(config["roles"], 1):
            frame = tk.Frame(self.players_container, bg=COLORS["bg_light"], relief=tk.RIDGE, bd=2)
            frame.pack(fill=tk.X, pady=2, padx=5)
            
            tk.Label(frame, text=f"玩家{i}", bg=COLORS["bg_light"], fg=COLORS["accent"],
                    width=8).pack(side=tk.LEFT, padx=5)
            
            role_var = tk.StringVar(value=role)
            ttk.Combobox(frame, textvariable=role_var,
                        values=["villager", "werewolf", "seer", "witch", "hunter", "guard"],
                        state="readonly", width=12).pack(side=tk.LEFT, padx=5)
            
            status = tk.Label(frame, text="⚪", bg=COLORS["bg_light"], fg=COLORS["text"], width=3)
            status.pack(side=tk.RIGHT, padx=5)
            
            self.player_frames.append({"seat": i, "role_var": role_var, "status": status, "alive": True})
        
        options = ["all"] + [f"玩家{p['seat']}" for p in self.player_frames]
        self.filter_combo['values'] = options
        
    def system_prompt(self, seat: int, role: str, teammates: List[int]) -> str:
        base = f"你是玩家{seat}号（{ROLE_NAMES.get(role, role)}）。"
        if role == "werewolf":
            base += f" 你的狼人队友是：{teammates}号。目标是伪装成村民。"
        elif role == "seer":
            base += " 你每晚可以查验一名玩家的身份。"
        elif role == "witch":
            base += " 你有一瓶解药和一瓶毒药。"
        elif role == "hunter":
            base += " 如果你被投票处决，可以开枪带走一人。"
        elif role == "guard":
            base += " 你每晚可以守护一名玩家（不能连续两晚守护同一人）。"
        else:
            base += " 你没有特殊能力，通过推理找出狼人。"
        return base
        
    def log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_display.insert(tk.END, f"[{timestamp}] {text}\n")
        self.log_display.see(tk.END)
        
    def add_chat_speech(self, seat: int, role: str, content: str, is_manual: bool = False):
        prefix = "✋ " if is_manual else "🎤 "
        self.chat_display.insert(tk.END, f"{prefix}玩家{seat}({ROLE_NAMES.get(role, role)}):\n{content}\n\n")
        self.chat_display.see(tk.END)
        
    def add_chat_vote(self, seat: int, role: str, target: int, is_manual: bool = False):
        prefix = "✋ " if is_manual else "🗳️ "
        self.chat_display.insert(tk.END, f"{prefix}玩家{seat}({ROLE_NAMES.get(role, role)}) 投票 → 投给 玩家{target}\n\n")
        self.chat_display.see(tk.END)
        
    def add_chat_system(self, content: str):
        self.chat_display.insert(tk.END, f"📢 {content}\n\n")
        self.chat_display.see(tk.END)
        
    def add_chat_elimination(self, seat: int, role: str, votes: int):
        self.chat_display.insert(tk.END, f"⚡ 玩家{seat}({ROLE_NAMES.get(role, role)}) 被投票处决！(得票: {votes})\n\n")
        self.chat_display.see(tk.END)
        
    def add_thought(self, seat: int, thought: str):
        if seat not in self.thoughts_history:
            self.thoughts_history[seat] = []
        self.thoughts_history[seat].append({"time": datetime.now().strftime("%H:%M:%S"), "thought": thought[:500]})
        if len(self.thoughts_history[seat]) > 50:
            self.thoughts_history[seat] = self.thoughts_history[seat][-50:]
        self.refresh_thoughts()
        
    def refresh_thoughts(self):
        self.thought_display.delete("1.0", tk.END)
        filter_val = self.filter_var.get()
        
        for seat, thoughts in self.thoughts_history.items():
            if filter_val != "all" and filter_val != f"玩家{seat}":
                continue
            self.thought_display.insert(tk.END, f"\n{'='*40}\n玩家{seat}\n{'='*40}\n")
            for t in thoughts[-10:]:
                self.thought_display.insert(tk.END, f"[{t['time']}]\n{t['thought']}\n\n")
        self.thought_display.see(tk.END)
        
    def update_status(self, seat: int, status: str):
        for p in self.player_frames:
            if p['seat'] == seat:
                p['status'].config(text=status)
                if status == "💀":
                    p['alive'] = False
                break
                
    def reset_game(self):
        self.game_running = False
        self.game_paused = False
        self.manual_speech.clear()
        self.manual_vote.clear()
        self.update_manual_status()
        for p in self.player_frames:
            p['alive'] = True
            p['status'].config(text="⚪")
        self.thoughts_history.clear()
        self.chat_display.delete("1.0", tk.END)
        self.thought_display.delete("1.0", tk.END)
        self.log_display.delete("1.0", tk.END)
        self.log("游戏已重置")
        self.add_chat_system("游戏已重置，可以重新开始")
        
    def toggle_pause(self):
        if not self.game_running:
            return
        
        self.game_paused = not self.game_paused
        if self.game_paused:
            self.pause_btn.config(text="继续", bg=COLORS["success"])
            self.add_chat_system("⏸ 游戏已暂停，可以提交手动接管指令")
            self.status_label.config(text="⏸ 已暂停", fg=COLORS["warning"])
        else:
            self.pause_btn.config(text="暂停", bg=COLORS["accent"])
            self.add_chat_system("▶ 游戏继续")
            self.status_label.config(text="🎮 游戏中", fg=COLORS["success"])
            
    def start_game(self):
        api_key = self.api_key_entry.get().strip()
        base_url = self.base_url_entry.get().strip()
        
        if not api_key:
            api_key = DEFAULT_API_KEY
        if not base_url:
            base_url = DEFAULT_BASE_URL
            
        for p in self.player_frames:
            p['alive'] = True
            p['status'].config(text="⚪")
            
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL)
        self.game_running = True
        self.game_paused = False
        self.manual_speech.clear()
        self.manual_vote.clear()
        self.update_manual_status()
        self.thoughts_history.clear()
        self.pause_btn.config(text="暂停", bg=COLORS["accent"])
        
        self.chat_display.delete("1.0", tk.END)
        self.thought_display.delete("1.0", tk.END)
        self.log_display.delete("1.0", tk.END)
        
        self.game_thread = threading.Thread(target=self.run_async_game, args=(api_key, base_url))
        self.game_thread.daemon = True
        self.game_thread.start()
        
    def run_async_game(self, api_key: str, base_url: str):
        asyncio.run(self.async_game_loop(api_key, base_url))
        
    async def async_game_loop(self, api_key: str, base_url: str):
        settings = GatewaySettings()
        settings.api_key = api_key
        settings.base_url = base_url
        settings.max_retries = 3
        settings.max_concurrency = 3
        settings.max_tokens_default = 600
        settings.max_tokens_hard_cap = 2048
        settings.timeout = 60
        
        request_delay = float(self.delay_var.get())
        
        try:
            async with LLMGateway(settings) as gw:
                game = GameSession(gw)
                wolf_seats = [p['seat'] for p in self.player_frames if p['role_var'].get() == 'werewolf']
                for p in self.player_frames:
                    role = p['role_var'].get()
                    teammates = [s for s in wolf_seats if s != p['seat']] if role == 'werewolf' else []
                    game.add_agent(p['seat'], role, self.system_prompt(p['seat'], role, teammates))
                
                self.log(f"游戏开始！{self.current_player_count}人局")
                self.log(f"请求间隔: {request_delay}秒/次")
                round_num = 1
                
                while self.game_running:
                    # 等待暂停恢复
                    while self.game_paused and self.game_running:
                        await asyncio.sleep(0.1)
                    
                    alive = [p for p in self.player_frames if p['alive']]
                    wolves = [p for p in alive if p['role_var'].get() == 'werewolf']
                    villagers = [p for p in alive if p['role_var'].get() != 'werewolf']
                    
                    if len(wolves) == 0:
                        self.add_chat_system("🏆 村民阵营获胜！所有狼人已被淘汰。")
                        break
                    if len(wolves) >= len(villagers):
                        self.add_chat_system("🏆 狼人阵营获胜！狼人数量不少于村民。")
                        break
                    if len(alive) <= 1:
                        break
                        
                    self.add_chat_system(f"\n========== 第{round_num}轮 ==========")
                    self.add_chat_system(f"🌞 白天阶段 - {len(alive)}名玩家发言")
                    alive_seats = [p['seat'] for p in alive]
                    
                    # 发言阶段
                    for p in alive:
                        if not self.game_running:
                            break
                        
                        # 等待暂停恢复
                        while self.game_paused and self.game_running:
                            await asyncio.sleep(0.1)
                        
                        self.update_status(p['seat'], "💭")
                        
                        # 检查是否有手动接管发言
                        if p['seat'] in self.manual_speech:
                            speech = self.manual_speech.pop(p['seat'])
                            self.update_manual_status()
                            self.add_chat_speech(p['seat'], p['role_var'].get(), speech, is_manual=True)
                            self.log(f"玩家{p['seat']} 使用手动接管发言")
                            self.update_status(p['seat'], "✅")
                            await asyncio.sleep(request_delay)
                            continue
                        
                        # 正常AI发言
                        try:
                            resp = await game.ask(p['seat'], f"白天发言。存活玩家:{alive_seats}。发表看法并分析谁是狼人。", max_tokens=500)
                            self.add_thought(p['seat'], resp.thought)
                            if resp.speech:
                                self.add_chat_speech(p['seat'], p['role_var'].get(), resp.speech)
                        except Exception as e:
                            error_msg = str(e)
                            self.log(f"玩家{p['seat']} 发言失败: {error_msg[:100]}")
                            self.add_chat_speech(p['seat'], p['role_var'].get(), "[发言失败]")
                        self.update_status(p['seat'], "✅")
                        await asyncio.sleep(request_delay)
                    
                    if not self.game_running:
                        break
                    
                    # 等待暂停恢复
                    while self.game_paused and self.game_running:
                        await asyncio.sleep(0.1)
                    
                    # 投票阶段
                    self.add_chat_system(f"🗳️ 投票阶段 - {len(alive)}人投票")
                    votes = {}
                    
                    for p in alive:
                        if not self.game_running:
                            break
                        
                        # 等待暂停恢复
                        while self.game_paused and self.game_running:
                            await asyncio.sleep(0.1)
                        
                        # 检查是否有手动接管投票
                        if p['seat'] in self.manual_vote:
                            target = self.manual_vote.pop(p['seat'])
                            self.update_manual_status()
                            votes[p['seat']] = target
                            self.add_chat_vote(p['seat'], p['role_var'].get(), target, is_manual=True)
                            self.log(f"玩家{p['seat']} 使用手动接管投票 → 玩家{target}")
                            await asyncio.sleep(request_delay)
                            continue
                        
                        # 正常AI投票
                        try:
                            resp = await game.ask(p['seat'], f"投票。存活玩家:{alive_seats}。投票给你认为最可能是狼人的玩家。", max_tokens=300)
                            self.add_thought(p['seat'], f"投票思考: {resp.thought[:200]}")
                            
                            if resp.action == "vote" and resp.target and resp.target in alive_seats:
                                votes[p['seat']] = resp.target
                                self.add_chat_vote(p['seat'], p['role_var'].get(), resp.target)
                                self.log(f"玩家{p['seat']} 投票给 玩家{resp.target}")
                            else:
                                self.log(f"玩家{p['seat']} 未有效投票")
                                
                        except Exception as e:
                            self.log(f"玩家{p['seat']} 投票失败: {str(e)[:100]}")
                            alive_others = [s for s in alive_seats if s != p['seat']]
                            if alive_others:
                                votes[p['seat']] = random.choice(alive_others)
                                self.add_chat_vote(p['seat'], p['role_var'].get(), votes[p['seat']])
                                self.log(f"玩家{p['seat']} 随机投票给 玩家{votes[p['seat']]}")
                        
                        await asyncio.sleep(request_delay)
                    
                    # 统计票数
                    if votes:
                        counts = {}
                        for t in votes.values():
                            counts[t] = counts.get(t, 0) + 1
                        
                        vote_summary = "📊 投票统计：\n"
                        for target, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
                            target_role = next((p['role_var'].get() for p in alive if p['seat'] == target), "未知")
                            vote_summary += f"   玩家{target}({ROLE_NAMES.get(target_role, target_role)}) 获得 {count} 票\n"
                        self.add_chat_system(vote_summary)
                        
                        eliminated = max(counts.items(), key=lambda x: x[1])[0]
                        eliminated_role = next((p['role_var'].get() for p in alive if p['seat'] == eliminated), "未知")
                        self.add_chat_elimination(eliminated, eliminated_role, counts[eliminated])
                        self.update_status(eliminated, "💀")
                        game.kill(eliminated)
                    else:
                        self.add_chat_system("⚠️ 无人投票，本轮无人出局")
                        
                    round_num += 1
                    await asyncio.sleep(2)
                    
                self.add_chat_system("游戏结束")
        except Exception as e:
            self.log(f"游戏循环出错: {str(e)}")
            self.add_chat_system(f"❌ 游戏出错: {str(e)[:200]}")
        finally:
            self.root.after(0, self.on_game_end)
            
    def stop_game(self):
        self.game_running = False
        self.update_status_text("已停止", "warning")
        self.log("游戏已停止")
        
    def on_game_end(self):
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        self.pause_btn.config(text="暂停", bg=COLORS["accent"])
        self.update_status_text("游戏结束", "success")
        
    def update_status_text(self, text: str, status_type: str = "info"):
        icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️"}
        self.status_label.config(text=f"{icons.get(status_type, 'ℹ️')} {text}")
        
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WerewolfObserverUI()
    app.run()