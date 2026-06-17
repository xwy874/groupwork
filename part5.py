import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
from datetime import datetime
from openai import OpenAI
import time

DEEPSEEK_API_KEY = "sk-7934eefa19634f5c859b1caa0cb57dbd"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

COLORS = {
    "bg_dark": "#1e1e2e",
    "bg_light": "#313244",
    "accent": "#89b4fa",
    "text": "#cdd6f4",
    "thought": "#a6e3a1",
    "output": "#f38ba8",
    "user": "#89b4fa",
    "warning": "#f38ba8",
    "success": "#a6e3a1"
}

ROLE_COLORS = [
    "#89b4fa", "#f9e2af", "#a6e3a1", "#f38ba8", "#cba6f7",
    "#94e2d5", "#fab387", "#f5c2e7", "#b4befe", "#74c7ec"
]


class MultiRoleSimulator:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self.roles = []
        self.conversation_history = []
        self.all_thoughts = []
        
    def add_role(self, name: str, description: str, color: str):
        self.roles.append({
            "name": name,
            "description": description,
            "color": color
        })
        
    def remove_role(self, index: int):
        if 0 <= index < len(self.roles):
            self.roles.pop(index)
            
    def update_role(self, index: int, name: str, description: str):
        if 0 <= index < len(self.roles):
            self.roles[index]["name"] = name
            self.roles[index]["description"] = description
            
    def get_role_response(self, role_index: int, context: str, callback=None):
        try:
            role = self.roles[role_index]
            
            system_prompt = f"""你正在扮演【{role['name']}】，角色描述如下：
{role['description']}

重要要求：
1. 完全按照这个角色的特点、说话方式、知识背景来回应
2. 保持角色一致性，不要跳出角色
3. 思考如何以该角色的视角回应对话
4. 回应要自然、真实，符合角色设定
5. 直接输出角色要说的话，不要添加任何旁白或说明"""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
            
            response = self.client.chat.completions.create(
                model="deepseek-reasoner",
                messages=messages,
                temperature=0.8,
                stream=False
            )
            
            assistant_message = response.choices[0].message.content
            
            thoughts = []
            if hasattr(response.choices[0].message, 'reasoning_content'):
                reasoning = response.choices[0].message.reasoning_content
                if reasoning:
                    thoughts = self._split_into_steps(reasoning)
            
            thought_record = {
                "timestamp": datetime.now(),
                "role": role['name'],
                "context": context[:200],
                "thoughts": thoughts,
                "response": assistant_message
            }
            self.all_thoughts.append(thought_record)
            
            if callback:
                callback(role['name'], thoughts, assistant_message, role['color'])
                
        except Exception as e:
            if callback:
                callback(role['name'], [f"错误: {str(e)}"], f"出错了: {str(e)}", "#f38ba8")
    
    def add_to_history(self, role_name: str, message: str):
        self.conversation_history.append({
            "role": role_name,
            "message": message,
            "timestamp": datetime.now()
        })
    
    def get_history_text(self):
        history_text = ""
        for entry in self.conversation_history:
            timestamp = entry["timestamp"].strftime("%H:%M:%S")
            history_text += f"[{timestamp}] {entry['role']}: {entry['message']}\n\n"
        return history_text
    
    def get_all_thoughts_text(self):
        if not self.all_thoughts:
            return "暂无思维链记录"
        
        text = ""
        for i, record in enumerate(self.all_thoughts, 1):
            timestamp = record["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            text += f"\n{'='*60}\n"
            text += f"📌 对话 {i} - {timestamp}\n"
            text += f"🎭 角色: {record['role']}\n"
            text += f"📝 对话上下文: {record['context']}...\n"
            text += f"🧠 思维链:\n"
            for j, thought in enumerate(record["thoughts"], 1):
                text += f"  步骤{j}: {thought}\n"
            text += f"💬 角色回复: {record['response']}\n"
            text += f"{'='*60}\n\n"
        
        return text
    
    def _split_into_steps(self, reasoning_text: str):
        steps = []
        import re
        sentences = re.split(r'[。！？；\n]+', reasoning_text)
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence and len(sentence) > 5:
                steps.append(sentence)
        return steps if steps else [reasoning_text]
    
    def reset(self):
        self.conversation_history = []
        self.all_thoughts = []


class DeepSeekAgent:
    def __init__(self, system_prompt: str, api_key: str):
        self.system_prompt = system_prompt
        self.conversation_history = [{"role": "system", "content": system_prompt}]
        self.client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self.all_thoughts = []
        
    def think_and_respond(self, user_input: str, callback=None):
        try:
            self.conversation_history.append({"role": "user", "content": user_input})
            
            response = self.client.chat.completions.create(
                model="deepseek-reasoner",
                messages=self.conversation_history,
                temperature=0.7,
                stream=False
            )
            
            assistant_message = response.choices[0].message.content
            
            thoughts = []
            if hasattr(response.choices[0].message, 'reasoning_content'):
                reasoning = response.choices[0].message.reasoning_content
                if reasoning:
                    thoughts = self._split_into_steps(reasoning)
            
            thought_record = {
                "timestamp": datetime.now(),
                "user_input": user_input,
                "thoughts": thoughts,
                "response": assistant_message
            }
            self.all_thoughts.append(thought_record)
            
            self.conversation_history.append({"role": "assistant", "content": assistant_message})
            
            if callback:
                callback(thoughts, assistant_message)
                
        except Exception as e:
            if callback:
                callback([f"错误: {str(e)}"], f"出错了: {str(e)}")
    
    def get_all_thoughts_text(self):
        if not self.all_thoughts:
            return "暂无思维链记录"
        
        text = ""
        for i, record in enumerate(self.all_thoughts, 1):
            timestamp = record["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            text += f"\n{'='*60}\n"
            text += f"📌 对话 {i} - {timestamp}\n"
            text += f"❓ 用户问题: {record['user_input']}\n"
            text += f"🧠 思维链:\n"
            for j, thought in enumerate(record["thoughts"], 1):
                text += f"  步骤{j}: {thought}\n"
            text += f"💬 AI回复: {record['response'][:200]}...\n"
            text += f"{'='*60}\n\n"
        
        return text
    
    def _split_into_steps(self, reasoning_text: str):
        steps = []
        import re
        sentences = re.split(r'[。！？；\n]+', reasoning_text)
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence and len(sentence) > 5:
                steps.append(sentence)
        return steps if steps else [reasoning_text]
    
    def reset(self):
        self.conversation_history = [{"role": "system", "content": self.system_prompt}]
        self.all_thoughts = []


class AutoDialogueManager:
    def __init__(self, agent: DeepSeekAgent, gui_callback):
        self.agent = agent
        self.gui_callback = gui_callback
        self.is_running = False
        self.stop_flag = False
        
    def start_auto_dialogue(self, initial_prompt: str, rounds: int = 5, interval: int = 3):
        if self.is_running:
            return
        
        self.is_running = True
        self.stop_flag = False
        thread = threading.Thread(target=self._auto_dialogue_loop, 
                                  args=(initial_prompt, rounds, interval))
        thread.daemon = True
        thread.start()
    
    def _auto_dialogue_loop(self, current_prompt: str, rounds: int, interval: int):
        for round_num in range(rounds):
            if self.stop_flag:
                break
            
            self.gui_callback("system", f"\n{'='*50}\n🔄 第 {round_num + 1} 轮对话\n{'='*50}")
            
            response_event = threading.Event()
            
            def on_response(thoughts, response):
                self.gui_callback("thoughts", thoughts)
                self.gui_callback("response", response)
                nonlocal current_prompt
                current_prompt = f"请继续讨论，基于你刚才的回答：{response[:100]}..."
                response_event.set()
            
            self.agent.think_and_respond(current_prompt, on_response)
            response_event.wait()
            
            if round_num < rounds - 1 and not self.stop_flag:
                for i in range(interval):
                    if self.stop_flag:
                        break
                    time.sleep(1)
        
        self.is_running = False
        self.gui_callback("finished", None)
    
    def stop(self):
        self.stop_flag = True


class AIObserverGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI智能体观察台 - 多角色模拟 v4.0")
        self.root.geometry("1600x1000")
        self.root.configure(bg=COLORS["bg_dark"])
        
        self.agent = None
        self.simulator = None
        self.is_thinking = False
        self.auto_manager = None
        
        self.setup_ui()
        self.setup_menu()
        self.init_default_roles()
        
    def init_default_roles(self):
        self.role_frames = []
        self.add_role_frame("专家", "你是一位经验丰富的技术专家，擅长用通俗易懂的方式解释复杂概念。")
        self.add_role_frame("初学者", "你是一位对技术感兴趣的初学者，充满好奇心，喜欢提问。")
        
    def setup_ui(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_frame = ttk.Frame(main_paned, width=400)
        main_paned.add(left_frame, weight=1)
        
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=3)
        
        left_notebook = ttk.Notebook(left_frame)
        left_notebook.pack(fill=tk.BOTH, expand=True)
        
        single_tab = ttk.Frame(left_notebook)
        left_notebook.add(single_tab, text="🤖 单角色模式")
        
        multi_tab = ttk.Frame(left_notebook)
        left_notebook.add(multi_tab, text="🎭 多角色模拟")
        
        single_canvas = tk.Canvas(single_tab, bg=COLORS["bg_dark"], highlightthickness=0)
        single_scrollbar = ttk.Scrollbar(single_tab, orient="vertical", command=single_canvas.yview)
        single_scrollable = ttk.Frame(single_canvas)
        
        single_scrollable.bind("<Configure>", lambda e: single_canvas.configure(scrollregion=single_canvas.bbox("all")))
        single_canvas.create_window((0, 0), window=single_scrollable, anchor="nw")
        single_canvas.configure(yscrollcommand=single_scrollbar.set)
        
        single_canvas.pack(side="left", fill="both", expand=True)
        single_scrollbar.pack(side="right", fill="y")
        
        tk.Label(single_scrollable, text="API Key:", 
                bg=COLORS["bg_dark"], fg=COLORS["text"], font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10,5), padx=10)
        
        self.api_key_entry = tk.Entry(single_scrollable, show="*", bg=COLORS["bg_light"], 
                                      fg=COLORS["text"], insertbackground=COLORS["text"])
        self.api_key_entry.pack(fill=tk.X, pady=5, padx=10)
        self.api_key_entry.insert(0, DEEPSEEK_API_KEY)
        
        tk.Label(single_scrollable, text="系统提示词:", 
                bg=COLORS["bg_dark"], fg=COLORS["text"], font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(10,5), padx=10)
        
        self.prompt_text = scrolledtext.ScrolledText(single_scrollable, height=6,
                                                      bg=COLORS["bg_light"], 
                                                      fg=COLORS["text"],
                                                      insertbackground=COLORS["text"])
        self.prompt_text.pack(fill=tk.X, pady=5, padx=10)
        self.prompt_text.insert("1.0", "你是一个乐于助人的AI助手，善于分析问题并给出详细的解答。")
        
        self.init_btn = tk.Button(single_scrollable, text="🚀 初始化智能体", 
                                  command=self.init_agent,
                                  bg=COLORS["accent"], fg=COLORS["bg_dark"],
                                  font=("Arial", 11, "bold"))
        self.init_btn.pack(pady=10, padx=10, fill=tk.X)
        
        self.reset_btn = tk.Button(single_scrollable, text="🔄 重置对话", 
                                   command=self.reset_conversation,
                                   bg=COLORS["bg_light"], fg=COLORS["text"],
                                   state=tk.DISABLED)
        self.reset_btn.pack(pady=5, padx=10, fill=tk.X)
        
        auto_frame = tk.LabelFrame(single_scrollable, text="🤖 AI自动持续对话", 
                                   bg=COLORS["bg_dark"], fg=COLORS["accent"])
        auto_frame.pack(fill=tk.X, pady=10, padx=10)
        
        tk.Label(auto_frame, text="对话轮数:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=10, pady=(10,5))
        self.auto_rounds = tk.Spinbox(auto_frame, from_=1, to=20, width=10, bg=COLORS["bg_light"], fg=COLORS["text"])
        self.auto_rounds.pack(anchor=tk.W, padx=10, pady=5)
        self.auto_rounds.delete(0, tk.END)
        self.auto_rounds.insert(0, "5")
        
        tk.Label(auto_frame, text="间隔时间(秒):", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=10, pady=(10,5))
        self.auto_interval = tk.Spinbox(auto_frame, from_=1, to=10, width=10, bg=COLORS["bg_light"], fg=COLORS["text"])
        self.auto_interval.pack(anchor=tk.W, padx=10, pady=5)
        self.auto_interval.delete(0, tk.END)
        self.auto_interval.insert(0, "3")
        
        tk.Label(auto_frame, text="初始话题:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W, padx=10, pady=(10,5))
        self.auto_topic = tk.Text(auto_frame, height=3, bg=COLORS["bg_light"], fg=COLORS["text"])
        self.auto_topic.pack(fill=tk.X, padx=10, pady=5)
        self.auto_topic.insert("1.0", "请介绍一下你自己，然后我们就这个话题深入讨论下去")
        
        btn_frame = tk.Frame(auto_frame, bg=COLORS["bg_dark"])
        btn_frame.pack(fill=tk.X, pady=10, padx=10)
        
        self.auto_start_btn = tk.Button(btn_frame, text="▶ 开始自动对话", 
                                        command=self.start_auto_dialogue,
                                        bg=COLORS["success"], fg=COLORS["bg_dark"],
                                        state=tk.DISABLED)
        self.auto_start_btn.pack(side=tk.LEFT, padx=(0,5), fill=tk.X, expand=True)
        
        self.auto_stop_btn = tk.Button(btn_frame, text="⏹ 停止自动对话", 
                                       command=self.stop_auto_dialogue,
                                       bg=COLORS["warning"], fg=COLORS["bg_dark"],
                                       state=tk.DISABLED)
        self.auto_stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.status_label = tk.Label(single_scrollable, text="⚪ 未初始化", 
                                     bg=COLORS["bg_dark"], fg=COLORS["text"])
        self.status_label.pack(pady=10)
        
        multi_canvas = tk.Canvas(multi_tab, bg=COLORS["bg_dark"], highlightthickness=0)
        multi_scrollbar = ttk.Scrollbar(multi_tab, orient="vertical", command=multi_canvas.yview)
        self.multi_scrollable = ttk.Frame(multi_canvas)
        
        self.multi_scrollable.bind("<Configure>", lambda e: multi_canvas.configure(scrollregion=multi_canvas.bbox("all")))
        multi_canvas.create_window((0, 0), window=self.multi_scrollable, anchor="nw")
        multi_canvas.configure(yscrollcommand=multi_scrollbar.set)
        
        multi_canvas.pack(side="left", fill="both", expand=True)
        multi_scrollbar.pack(side="right", fill="y")
        
        title_frame = tk.Frame(self.multi_scrollable, bg=COLORS["bg_dark"])
        title_frame.pack(fill=tk.X, pady=10, padx=10)
        
        tk.Label(title_frame, text="🎭 角色列表", 
                font=("Arial", 14, "bold"),
                bg=COLORS["bg_dark"], fg=COLORS["accent"]).pack(side=tk.LEFT)
        
        self.add_role_btn = tk.Button(title_frame, text="+ 添加角色", 
                                      command=self.add_new_role,
                                      bg=COLORS["success"], fg=COLORS["bg_dark"],
                                      font=("Arial", 10, "bold"))
        self.add_role_btn.pack(side=tk.RIGHT)
        
        self.roles_container = tk.Frame(self.multi_scrollable, bg=COLORS["bg_dark"])
        self.roles_container.pack(fill=tk.BOTH, expand=True, pady=10)
        
        control_frame = tk.Frame(self.multi_scrollable, bg=COLORS["bg_dark"])
        control_frame.pack(fill=tk.X, pady=10, padx=10)
        
        self.init_multi_btn = tk.Button(control_frame, text="🎭 初始化多角色模拟器", 
                                        command=self.init_multi_simulator,
                                        bg=COLORS["accent"], fg=COLORS["bg_dark"],
                                        font=("Arial", 11, "bold"))
        self.init_multi_btn.pack(fill=tk.X, pady=5)
        
        self.reset_multi_btn = tk.Button(control_frame, text="🔄 重置多角色对话", 
                                         command=self.reset_multi_simulation,
                                         bg=COLORS["bg_light"], fg=COLORS["text"],
                                         state=tk.DISABLED)
        self.reset_multi_btn.pack(fill=tk.X, pady=5)
        
        right_notebook = ttk.Notebook(right_frame)
        right_notebook.pack(fill=tk.BOTH, expand=True)
        
        single_display_tab = ttk.Frame(right_notebook)
        right_notebook.add(single_display_tab, text="🤖 单角色对话")
        
        multi_display_tab = ttk.Frame(right_notebook)
        right_notebook.add(multi_display_tab, text="🎭 多角色对话")
        
        history_tab = ttk.Frame(right_notebook)
        right_notebook.add(history_tab, text="📚 历史思维链")
        
        single_display_notebook = ttk.Notebook(single_display_tab)
        single_display_notebook.pack(fill=tk.BOTH, expand=True)
        
        thoughts_frame = ttk.Frame(single_display_notebook)
        single_display_notebook.add(thoughts_frame, text="🧠 当前思维链")
        
        self.thoughts_display = scrolledtext.ScrolledText(thoughts_frame, 
                                                           bg=COLORS["bg_light"],
                                                           fg=COLORS["thought"],
                                                           font=("Consolas", 11),
                                                           wrap=tk.WORD)
        self.thoughts_display.pack(fill=tk.BOTH, expand=True)
        
        output_frame = ttk.Frame(single_display_notebook)
        single_display_notebook.add(output_frame, text="💬 AI回复")
        
        self.output_display = scrolledtext.ScrolledText(output_frame,
                                                         bg=COLORS["bg_light"],
                                                         fg=COLORS["output"],
                                                         font=("Arial", 11),
                                                         wrap=tk.WORD)
        self.output_display.pack(fill=tk.BOTH, expand=True)
        
        history_frame = ttk.Frame(single_display_notebook)
        single_display_notebook.add(history_frame, text="📝 对话历史")
        
        self.history_display = scrolledtext.ScrolledText(history_frame,
                                                          bg=COLORS["bg_light"],
                                                          fg=COLORS["text"],
                                                          font=("Arial", 10),
                                                          wrap=tk.WORD)
        self.history_display.pack(fill=tk.BOTH, expand=True)
        
        input_frame = ttk.Frame(single_display_tab)
        input_frame.pack(fill=tk.X, pady=(10, 0))
        
        tk.Label(input_frame, text="💬 你的问题:", bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor=tk.W)
        
        self.input_text = scrolledtext.ScrolledText(input_frame, height=3,
                                                     bg=COLORS["bg_light"],
                                                     fg=COLORS["text"])
        self.input_text.pack(fill=tk.X, pady=5)
        
        button_frame = ttk.Frame(input_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        self.send_btn = tk.Button(button_frame, text="📤 发送 (Ctrl+Enter)", 
                                  command=self.send_message,
                                  bg=COLORS["user"], fg=COLORS["bg_dark"],
                                  state=tk.DISABLED)
        self.send_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.clear_btn = tk.Button(button_frame, text="🗑️ 清空输入", 
                                   command=self.clear_input,
                                   bg=COLORS["bg_light"], fg=COLORS["text"])
        self.clear_btn.pack(side=tk.LEFT)
        
        multi_display_frame = ttk.Frame(multi_display_tab)
        multi_display_frame.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(multi_display_frame, text="🎭 多角色对话记录", 
                font=("Arial", 12, "bold"),
                bg=COLORS["bg_dark"], fg=COLORS["accent"]).pack(anchor=tk.W, pady=(0, 5))
        
        self.multi_history_display = scrolledtext.ScrolledText(multi_display_frame,
                                                                 bg=COLORS["bg_light"],
                                                                 fg=COLORS["text"],
                                                                 font=("Arial", 11),
                                                                 wrap=tk.WORD,
                                                                 height=12)
        self.multi_history_display.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        multi_thoughts_frame = tk.LabelFrame(multi_display_frame, text="🧠 当前角色思维链", 
                                             bg=COLORS["bg_dark"], fg=COLORS["accent"])
        multi_thoughts_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.multi_thoughts_display = scrolledtext.ScrolledText(multi_thoughts_frame,
                                                                  bg=COLORS["bg_light"],
                                                                  fg=COLORS["thought"],
                                                                  font=("Consolas", 10),
                                                                  wrap=tk.WORD,
                                                                  height=6)
        self.multi_thoughts_display.pack(fill=tk.BOTH, expand=True)
        
        control_panel = tk.Frame(multi_display_frame, bg=COLORS["bg_dark"])
        control_panel.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(control_panel, text="选择发言角色:", 
                bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(side=tk.LEFT, padx=(0, 10))
        
        self.speaking_role_var = tk.StringVar(value="0")
        self.role_selector = ttk.Combobox(control_panel, textvariable=self.speaking_role_var, 
                                          state="readonly", width=20)
        self.role_selector.pack(side=tk.LEFT, padx=(0, 10))
        
        self.multi_speak_btn = tk.Button(control_panel, text="🎭 角色发言", 
                                         command=self.multi_role_speak,
                                         bg=COLORS["accent"], fg=COLORS["bg_dark"],
                                         state=tk.DISABLED)
        self.multi_speak_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.multi_auto_btn = tk.Button(control_panel, text="🔄 自动对话一轮", 
                                        command=self.multi_auto_dialogue,
                                        bg=COLORS["user"], fg=COLORS["bg_dark"],
                                        state=tk.DISABLED)
        self.multi_auto_btn.pack(side=tk.LEFT)
        
        self.multi_input = scrolledtext.ScrolledText(multi_display_frame, height=3,
                                                       bg=COLORS["bg_light"],
                                                       fg=COLORS["text"])
        self.multi_input.pack(fill=tk.X, pady=(0, 10))
        
        history_notebook = ttk.Notebook(history_tab)
        history_notebook.pack(fill=tk.BOTH, expand=True)
        
        single_history_frame = ttk.Frame(history_notebook)
        history_notebook.add(single_history_frame, text="🤖 单角色思维链")
        
        self.single_thoughts_history = scrolledtext.ScrolledText(single_history_frame,
                                                                   bg=COLORS["bg_light"],
                                                                   fg=COLORS["thought"],
                                                                   font=("Consolas", 10))
        self.single_thoughts_history.pack(fill=tk.BOTH, expand=True)
        
        multi_history_frame = ttk.Frame(history_notebook)
        history_notebook.add(multi_history_frame, text="🎭 多角色思维链")
        
        self.multi_thoughts_history = scrolledtext.ScrolledText(multi_history_frame,
                                                                  bg=COLORS["bg_light"],
                                                                  fg=COLORS["thought"],
                                                                  font=("Consolas", 10))
        self.multi_thoughts_history.pack(fill=tk.BOTH, expand=True)
        
        refresh_btn = tk.Button(history_tab, text="🔄 刷新历史记录", 
                                command=self.refresh_thoughts_history,
                                bg=COLORS["accent"], fg=COLORS["bg_dark"])
        refresh_btn.pack(pady=10)
        
        self.input_text.bind("<Control-Return>", lambda e: self.send_message())
        self.multi_input.bind("<Control-Return>", lambda e: self.multi_role_speak())
    
    def add_new_role(self):
        role_num = len(self.role_frames) + 1
        self.add_role_frame(f"角色{role_num}", f"请描述角色{role_num}的性格、背景和说话方式")
    
    def add_role_frame(self, default_name="新角色", default_desc="角色描述"):
        frame = tk.LabelFrame(self.roles_container, text=f"角色 {len(self.role_frames) + 1}", 
                              bg=COLORS["bg_dark"], fg=COLORS["accent"])
        frame.pack(fill=tk.X, pady=5, padx=10)
        
        name_frame = tk.Frame(frame, bg=COLORS["bg_dark"])
        name_frame.pack(fill=tk.X, pady=5, padx=10)
        
        tk.Label(name_frame, text="名称:", bg=COLORS["bg_dark"], fg=COLORS["text"], width=6).pack(side=tk.LEFT)
        name_entry = tk.Entry(name_frame, bg=COLORS["bg_light"], fg=COLORS["text"])
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        name_entry.insert(0, default_name)
        
        desc_label = tk.Label(frame, text="角色描述:", bg=COLORS["bg_dark"], fg=COLORS["text"])
        desc_label.pack(anchor=tk.W, padx=10, pady=(5,0))
        
        desc_text = scrolledtext.ScrolledText(frame, height=4, bg=COLORS["bg_light"], fg=COLORS["text"])
        desc_text.pack(fill=tk.X, padx=10, pady=5)
        desc_text.insert("1.0", default_desc)
        
        def delete_role():
            if len(self.role_frames) <= 2:
                messagebox.showwarning("警告", "至少需要保留2个角色")
                return
            frame.destroy()
            self.role_frames.remove(frame)
            self.update_role_numbers()
        
        delete_btn = tk.Button(frame, text="删除角色", command=delete_role,
                               bg=COLORS["warning"], fg=COLORS["bg_dark"])
        delete_btn.pack(pady=5, padx=10, fill=tk.X)
        
        self.role_frames.append({
            "frame": frame,
            "name_entry": name_entry,
            "desc_text": desc_text
        })
        
        self.update_role_numbers()
        self.update_role_selector()
    
    def update_role_numbers(self):
        for i, role in enumerate(self.role_frames):
            role["frame"].config(text=f"角色 {i + 1}")
    
    def update_role_selector(self):
        roles = [f"{i+1}. {role['name_entry'].get()}" for i, role in enumerate(self.role_frames)]
        self.role_selector['values'] = roles
        if roles:
            self.role_selector.set(roles[0])
            self.speaking_role_var.set("0")
    
    def init_multi_simulator(self):
        api_key = self.api_key_entry.get().strip()
        if not api_key:
            messagebox.showwarning("警告", "请输入API Key")
            return
        
        if len(self.role_frames) < 2:
            messagebox.showwarning("警告", "至少需要2个角色")
            return
        
        try:
            self.simulator = MultiRoleSimulator(api_key)
            
            for role in self.role_frames:
                name = role["name_entry"].get().strip()
                desc = role["desc_text"].get("1.0", tk.END).strip()
                color = ROLE_COLORS[len(self.simulator.roles) % len(ROLE_COLORS)]
                self.simulator.add_role(name, desc, color)
            
            self.multi_speak_btn.config(state=tk.NORMAL)
            self.multi_auto_btn.config(state=tk.NORMAL)
            self.reset_multi_btn.config(state=tk.NORMAL)
            
            self.update_status("✅ 多角色模拟器已就绪", "success")
            
            self.multi_history_display.delete("1.0", tk.END)
            self.multi_thoughts_display.delete("1.0", tk.END)
            
            welcome = "🎭 多角色模拟已启动\n\n"
            for i, role in enumerate(self.simulator.roles, 1):
                welcome += f"角色{i}: {role['name']}\n"
            welcome += "\n开始对话吧！"
            self.multi_history_display.insert(tk.END, welcome)
            
        except Exception as e:
            messagebox.showerror("错误", f"初始化失败: {str(e)}")
            self.update_status("❌ 初始化失败", "error")
    
    def multi_role_speak(self):
        if not self.simulator:
            messagebox.showinfo("提示", "请先初始化模拟器")
            return
        
        if self.is_thinking:
            return
        
        message = self.multi_input.get("1.0", tk.END).strip()
        if not message:
            messagebox.showwarning("警告", "请输入发言内容")
            return
        
        self.multi_input.delete("1.0", tk.END)
        
        role_index = int(self.speaking_role_var.get().split(".")[0]) - 1
        
        role = self.simulator.roles[role_index]
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.multi_history_display.insert(tk.END, f"[{timestamp}] {role['name']} (用户控制):\n")
        self.multi_history_display.insert(tk.END, f"{message}\n\n")
        self.multi_history_display.see(tk.END)
        
        self.simulator.add_to_history(role['name'], message)
        
        self.multi_thoughts_display.delete("1.0", tk.END)
        
        self.is_thinking = True
        self.update_status(f"🤔 {role['name']} 正在思考如何回应...", "info")
        
        context = self.simulator.get_history_text()
        
        other_indices = [i for i in range(len(self.simulator.roles)) if i != role_index]
        import random
        other_index = random.choice(other_indices)
        
        def callback(role_name, thoughts, response, color):
            self.root.after(0, self.display_multi_response, role_name, thoughts, response, color)
        
        thread = threading.Thread(target=self.simulator.get_role_response,
                                  args=(other_index, context, callback))
        thread.daemon = True
        thread.start()
    
    def display_multi_response(self, role_name, thoughts, response, color):
        self.multi_thoughts_display.delete("1.0", tk.END)
        self.multi_thoughts_display.insert(tk.END, f"🤔 {role_name} 的思考过程:\n\n")
        
        for i, thought in enumerate(thoughts, 1):
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.multi_thoughts_display.insert(tk.END, f"[{timestamp}] 思考步骤 {i}:\n")
            self.multi_thoughts_display.insert(tk.END, f"  {thought}\n\n")
        
        self.multi_thoughts_display.see(tk.END)
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.multi_history_display.insert(tk.END, f"[{timestamp}] {role_name}:\n")
        self.multi_history_display.insert(tk.END, f"{response}\n\n")
        self.multi_history_display.see(tk.END)
        
        self.simulator.add_to_history(role_name, response)
        
        self.is_thinking = False
        self.update_status("✅ 回复完成", "success")
        self.refresh_thoughts_history()
    
    def multi_auto_dialogue(self):
        if not self.simulator:
            messagebox.showinfo("提示", "请先初始化模拟器")
            return
        
        if self.is_thinking or len(self.simulator.roles) < 2:
            return
        
        self.is_thinking = True
        self.update_status("🤔 自动对话进行中...", "info")
        self.multi_thoughts_display.delete("1.0", tk.END)
        
        context = self.simulator.get_history_text()
        if not context.strip():
            first_role = self.simulator.roles[0]
            context = f"开始对话。{first_role['name']} 说：你好！"
        
        import random
        role_indices = list(range(len(self.simulator.roles)))
        random.shuffle(role_indices)
        
        self._auto_dialogue_step(role_indices, 0, context)
    
    def _auto_dialogue_step(self, role_indices, step, context):
        if step >= len(role_indices) or self.is_thinking == False:
            self.is_thinking = False
            self.update_status("✅ 自动对话完成", "success")
            self.refresh_thoughts_history()
            return
        
        role_index = role_indices[step]
        
        def callback(role_name, thoughts, response, color):
            self.multi_thoughts_display.insert(tk.END, f"🤔 {role_name} 的思考过程:\n\n")
            for i, thought in enumerate(thoughts, 1):
                timestamp = datetime.now().strftime("%H:%M:%S")
                self.multi_thoughts_display.insert(tk.END, f"[{timestamp}] 思考步骤 {i}:\n")
                self.multi_thoughts_display.insert(tk.END, f"  {thought}\n\n")
            self.multi_thoughts_display.see(tk.END)
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.multi_history_display.insert(tk.END, f"[{timestamp}] {role_name}:\n")
            self.multi_history_display.insert(tk.END, f"{response}\n\n")
            self.multi_history_display.see(tk.END)
            
            self.simulator.add_to_history(role_name, response)
            
            new_context = self.simulator.get_history_text()
            self.root.after(1000, lambda: self._auto_dialogue_step(role_indices, step + 1, new_context))
        
        self.simulator.get_role_response(role_index, context, callback)
    
    def reset_multi_simulation(self):
        if self.simulator:
            self.simulator.reset()
            self.multi_history_display.delete("1.0", tk.END)
            self.multi_thoughts_display.delete("1.0", tk.END)
            
            welcome = "🎭 多角色对话已重置\n\n"
            for i, role in enumerate(self.simulator.roles, 1):
                welcome += f"角色{i}: {role['name']}\n"
            welcome += "\n开始新的对话吧！"
            self.multi_history_display.insert(tk.END, welcome)
            
            self.update_status("🔄 多角色对话已重置", "info")
            self.refresh_thoughts_history()
    
    def init_agent(self):
        api_key = self.api_key_entry.get().strip()
        if not api_key:
            messagebox.showwarning("警告", "请输入API Key")
            return
        
        system_prompt = self.prompt_text.get("1.0", tk.END).strip()
        
        try:
            self.agent = DeepSeekAgent(system_prompt, api_key)
            self.send_btn.config(state=tk.NORMAL)
            self.reset_btn.config(state=tk.NORMAL)
            self.auto_start_btn.config(state=tk.NORMAL)
            self.auto_stop_btn.config(state=tk.NORMAL)
            self.update_status("✅ 单角色智能体已就绪", "success")
            
            self.auto_manager = AutoDialogueManager(self.agent, self.auto_callback)
            
            self.thoughts_display.delete("1.0", tk.END)
            self.output_display.delete("1.0", tk.END)
            
            self.add_to_history("系统", "智能体已初始化，可以开始对话了！")
            
        except Exception as e:
            messagebox.showerror("错误", f"初始化失败: {str(e)}")
            self.update_status("❌ 初始化失败", "error")
    
    def auto_callback(self, msg_type, data):
        if msg_type == "system":
            self.root.after(0, lambda: self.add_to_history("系统", data))
        elif msg_type == "thoughts":
            self.root.after(0, lambda: self.display_thoughts(data))
        elif msg_type == "response":
            self.root.after(0, lambda: self.display_response(data))
        elif msg_type == "finished":
            self.root.after(0, lambda: self.update_status("✅ 自动对话完成", "success"))
    
    def start_auto_dialogue(self):
        if not self.agent:
            messagebox.showinfo("提示", "请先初始化智能体")
            return
        
        if self.auto_manager and self.auto_manager.is_running:
            messagebox.showinfo("提示", "自动对话已在运行中")
            return
        
        rounds = int(self.auto_rounds.get())
        interval = int(self.auto_interval.get())
        topic = self.auto_topic.get("1.0", tk.END).strip()
        
        if not topic:
            messagebox.showwarning("警告", "请输入初始话题")
            return
        
        self.add_to_history("系统", f"🚀 开始自动对话 - 共{rounds}轮，间隔{interval}秒")
        self.add_to_history("系统", f"📝 初始话题: {topic}")
        self.update_status("🤖 自动对话进行中...", "info")
        
        self.auto_manager.start_auto_dialogue(topic, rounds, interval)
    
    def stop_auto_dialogue(self):
        if self.auto_manager and self.auto_manager.is_running:
            self.auto_manager.stop()
            self.add_to_history("系统", "⏹ 自动对话已停止")
            self.update_status("⏹ 自动对话已停止", "warning")
    
    def display_thoughts(self, thoughts):
        self.thoughts_display.delete("1.0", tk.END)
        self.thoughts_display.insert(tk.END, "🧠 AI思考过程:\n\n")
        for i, thought in enumerate(thoughts, 1):
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.thoughts_display.insert(tk.END, f"[{timestamp}] 步骤 {i}:\n")
            self.thoughts_display.insert(tk.END, f"  {thought}\n\n")
        self.thoughts_display.see(tk.END)
    
    def display_response(self, response):
        self.output_display.delete("1.0", tk.END)
        self.output_display.insert(tk.END, response)
        self.output_display.see(tk.END)
        self.add_to_history("AI", response)
    
    def refresh_thoughts_history(self):
        if self.agent:
            self.single_thoughts_history.delete("1.0", tk.END)
            self.single_thoughts_history.insert("1.0", self.agent.get_all_thoughts_text())
        
        if self.simulator:
            self.multi_thoughts_history.delete("1.0", tk.END)
            self.multi_thoughts_history.insert("1.0", self.simulator.get_all_thoughts_text())
    
    def reset_conversation(self):
        if self.agent:
            self.agent.reset()
            self.thoughts_display.delete("1.0", tk.END)
            self.output_display.delete("1.0", tk.END)
            self.history_display.delete("1.0", tk.END)
            self.add_to_history("系统", "对话已重置")
            self.update_status("🔄 单角色对话已重置", "info")
            self.refresh_thoughts_history()
    
    def send_message(self):
        if not self.agent:
            messagebox.showinfo("提示", "请先初始化智能体")
            return
        
        if self.is_thinking:
            return
        
        user_input = self.input_text.get("1.0", tk.END).strip()
        if not user_input:
            return
        
        self.input_text.delete("1.0", tk.END)
        self.add_to_history("你", user_input)
        
        self.send_btn.config(state=tk.DISABLED)
        self.is_thinking = True
        self.update_status("🤔 AI正在思考...", "info")
        
        self.thoughts_display.delete("1.0", tk.END)
        self.output_display.delete("1.0", tk.END)
        
        thread = threading.Thread(target=self.agent.think_and_respond, 
                                  args=(user_input, self.on_response_received))
        thread.daemon = True
        thread.start()
    
    def on_response_received(self, thoughts, response):
        self.root.after(0, self.update_display, thoughts, response)
    
    def update_display(self, thoughts, response):
        self.thoughts_display.insert(tk.END, "🧠 AI思考过程:\n\n")
        for i, thought in enumerate(thoughts, 1):
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.thoughts_display.insert(tk.END, f"[{timestamp}] 步骤 {i}:\n")
            self.thoughts_display.insert(tk.END, f"  {thought}\n\n")
        
        self.output_display.insert(tk.END, response)
        self.add_to_history("AI", response)
        
        self.send_btn.config(state=tk.NORMAL)
        self.is_thinking = False
        self.update_status("✅ 思考完成", "success")
        
        self.thoughts_display.see(tk.END)
        self.output_display.see(tk.END)
        self.refresh_thoughts_history()
    
    def add_to_history(self, sender, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.history_display.insert(tk.END, f"[{timestamp}] {sender}:\n")
        self.history_display.insert(tk.END, f"{message}\n\n")
        self.history_display.see(tk.END)
    
    def update_status(self, message, status_type="info"):
        icons = {"info": "ℹ️", "success": "✅", "error": "❌", "warning": "⚠️"}
        icon = icons.get(status_type, "ℹ️")
        self.status_label.config(text=f"{icon} {message}")
    
    def clear_input(self):
        self.input_text.delete("1.0", tk.END)
    
    def setup_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="导出单角色对话", command=self.export_history)
        file_menu.add_command(label="导出多角色对话", command=self.export_multi_history)
        file_menu.add_command(label="导出思维链历史", command=self.export_thoughts_history)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self.show_about)
    
    def export_history(self):
        from tkinter import filedialog
        
        content = self.history_display.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("提示", "没有可导出的内容")
            return
        
        file_path = filedialog.asksaveasfilename(defaultextension=".txt")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            messagebox.showinfo("成功", f"对话已导出到:\n{file_path}")
    
    def export_multi_history(self):
        from tkinter import filedialog
        
        content = self.multi_history_display.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("提示", "没有可导出的内容")
            return
        
        file_path = filedialog.asksaveasfilename(defaultextension=".txt")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            messagebox.showinfo("成功", f"对话已导出到:\n{file_path}")
    
    def export_thoughts_history(self):
        from tkinter import filedialog
        
        content = ""
        if self.agent:
            content += "=== 单角色思维链历史 ===\n"
            content += self.agent.get_all_thoughts_text()
        
        if self.simulator:
            content += "\n=== 多角色思维链历史 ===\n"
            content += self.simulator.get_all_thoughts_text()
        
        if not content.strip():
            messagebox.showinfo("提示", "没有可导出的内容")
            return
        
        file_path = filedialog.asksaveasfilename(defaultextension=".txt")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            messagebox.showinfo("成功", f"思维链历史已导出到:\n{file_path}")
    
    def show_about(self):
        about_text = """AI智能体观察台 v4.0

✨ 新功能：完全自定义角色数量！

功能说明：
• 单角色模式：传统的一对一AI对话
• 多角色模拟模式：
  - 可动态添加/删除角色
  - 每个角色独立设置人设
  - 支持任意数量角色（至少2个）
  - 手动控制任意角色发言
  - 自动对话一轮（随机顺序）

使用方法：
1. 在多角色模式标签页点击"+ 添加角色"
2. 设置每个角色的名称和人设
3. 点击"初始化多角色模拟器"
4. 选择要发言的角色，输入内容
5. 系统会自动让另一个角色回应

使用DeepSeek API
"""
        messagebox.showinfo("关于", about_text)
    
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print("启动AI智能体观察台 v4.0...")
    print("✨ 新功能：完全自定义角色数量！")
    print("  - 点击'+ 添加角色'按钮可添加任意数量角色")
    print("  - 每个角色可独立设置名称和人设")
    app = AIObserverGUI()
    app.run()
