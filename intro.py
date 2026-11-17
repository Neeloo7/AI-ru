import os
import json
from pathlib import Path
import customtkinter as ctk

CONFIG_PATH = Path(__file__).resolve().with_name("config.json")

class AiruIntroWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("AI-ru // First-Launch Setup")
        self.geometry("520x620")
        self.resizable(False, False)
        
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        # Center the window on screen
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')
        
        self.companion_name = "AI-ru"
        self.selected_voice = "Clean & Crisp (Neutral)"
        self.setup_ui()

    def setup_ui(self):
        # Scrollable frame to keep everything neatly contained
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", width=480, height=580)
        self.scroll_frame.pack(padx=20, pady=20, fill="both", expand=True)

        # 1. Welcome Message
        welcome_text = (
            "\"Hey there! Systems are fully online and I'm ready to settle in. "
            "I'll be your local desktop companion—handling your apps, keeping things running smooth, "
            "and hanging out right here on your screen.\n\n"
            "Before we dive in, I need a couple of quick things...\""
        )
        self.welcome_label = ctk.CTkLabel(
            self.scroll_frame, 
            text=welcome_text, 
            font=("Segoe UI", 13, "italic"),
            justify="left",
            wraplength=440
        )
        self.welcome_label.pack(pady=(10, 20), anchor="w")

        # 2. Name Section
        self.name_title = ctk.CTkLabel(
            self.scroll_frame, 
            text="1. What are you going to call me?", 
            font=("Segoe UI", 14, "bold")
        )
        self.name_title.pack(anchor="w", pady=(0, 5))

        self.name_entry = ctk.CTkEntry(
            self.scroll_frame, 
            placeholder_text="e.g., AI-ru, Nova, Echo, Atlas...", 
            width=440, 
            height=40,
            font=("Segoe UI", 13)
        )
        self.name_entry.pack(anchor="w", pady=(0, 2))
        self.name_entry.insert(0, "AI-ru")

        self.name_hint = ctk.CTkLabel(
            self.scroll_frame, 
            text="You can name me whatever you like—I won't judge.", 
            font=("Segoe UI", 11, "normal"),
            text_color="gray"
        )
        self.name_hint.pack(anchor="w", pady=(0, 20))

        # 3. Voice Customization Section
        self.voice_title = ctk.CTkLabel(
            self.scroll_frame, 
            text="2. Pick a voice that fits the vibe:", 
            font=("Segoe UI", 14, "bold")
        )
        self.voice_title.pack(anchor="w", pady=(0, 10))

        self.voice_var = ctk.StringVar(value="neutral_echo")

        voices = [
    ("Atlas — Deep & Clean (Male Profile)", "male_atlas"),
    ("Nova — Warm & Bright (Female Profile)", "female_nova"),
    ("Echo — Smooth & Balanced (Neutral Profile)", "neutral_echo")
]

        for display_text, val in voices:
            rb = ctk.CTkRadioButton(
                self.scroll_frame, 
                text=display_text, 
                variable=self.voice_var, 
                value=val,
                font=("Segoe UI", 12)
            )
            rb.pack(anchor="w", pady=6)

        # Spacer
        self.spacer = ctk.CTkLabel(self.scroll_frame, text="", height=10)
        self.spacer.pack()

        # 4. Initialize Button
        self.submit_btn = ctk.CTkButton(
            self.scroll_frame, 
            text="Initialize Companion", 
            command=self.save_and_launch,
            width=440,
            height=45,
            font=("Segoe UI", 14, "bold")
        )
        self.submit_btn.pack(pady=(10, 20))

    def save_and_launch(self):
        entered_name = self.name_entry.get().strip()
        if entered_name:
            self.companion_name = entered_name
        
        self.selected_voice = self.voice_var.get()
        
        # Save to config.json
        config_data = {
            "companion_name": self.companion_name,
            "voice_profile": self.selected_voice,
            "first_run": False
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")
            
        self.destroy()

def run_intro_if_needed():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                if not data.get("first_run", True):
                    return data.get("companion_name", "AI-ru")
        except:
            pass
            
    # If first run, launch intro window
    app = AiruIntroWindow()
    app.mainloop()
    
    # Read back the saved name
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f).get("companion_name", "AI-ru")
    return "AI-ru"
