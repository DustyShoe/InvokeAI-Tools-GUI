from pathlib import Path
from contextlib import closing
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yaml

REPO = Path(__file__).resolve().parent
OPERATIONS = {
    'restore': ('Restore Images Database', 'scripts/Restore_Images_DB_v3.1.py'),
    'assets': ('Convert Board to Assets', 'scripts/Convert_Board_to_Assets_v1.1.py'),
    'board': ('Convert Assets to Board', 'scripts/Convert_Assets_to_Board_v1.1.py'),
    'png': ('Recompress PNG', 'scripts/png_recompress_level9_v1.0.py'),
}


def configured_paths(root):
    file = root / 'invokeai.yaml'
    config = (yaml.safe_load(file.read_text(encoding='utf-8-sig')) or {}) if file.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError('Invalid invokeai.yaml: expected a mapping')
    legacy = (config.get('InvokeAI') or {}).get('Paths') or {}

    def directory(keys, default):
        value = next((mapping[key] for mapping in (legacy, config) for key in keys
                      if mapping.get(key) is not None), default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Invalid path setting: {keys[0]}')
        path = Path(value).expanduser()
        return path if path.is_absolute() else root / path

    return directory(('db_dir',), 'databases') / 'invokeai.db', directory(('outdir', 'outputs_dir'), 'outputs') / 'images'


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('InvokeAI Database Tools')
        self.geometry('760x650')
        self.minsize(680, 580)
        self.configure(fg_color='#181c20')
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self.root_path = tk.StringVar()
        self.operation = tk.StringVar(value='restore')
        self.dry_run = tk.BooleanVar(value=True)
        self.events = queue.Queue()
        self.running = False
        self.stop_file = None
        self.stop_pending = False
        self.boards = []
        self.loaded_root = None
        self.controls = []
        self.menus = {}
        ctk.CTkLabel(self, text='InvokeAI Database Tools', font=('Segoe UI', 18, 'bold'), anchor='w').grid(row=0, column=0, sticky='ew', padx=24, pady=(20, 16))
        paths = ctk.CTkFrame(self, fg_color='transparent')
        paths.grid(row=1, column=0, sticky='ew', padx=24)
        paths.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(paths, text='InvokeAI root directory').grid(row=0, column=0, sticky='w')
        entry = ctk.CTkEntry(paths, textvariable=self.root_path, height=36)
        entry.grid(row=1, column=0, sticky='ew', pady=(4, 12))
        entry.bind('<Return>', lambda _: self.load_boards())
        entry.bind('<FocusOut>', lambda _: self.load_boards())
        browse = ctk.CTkButton(paths, text='Browse', width=92, height=36, command=self.browse)
        browse.grid(row=1, column=1, padx=(10, 0), pady=(4, 12))
        self.controls.extend([entry, browse])
        operations = ctk.CTkFrame(self, fg_color='transparent')
        operations.grid(row=2, column=0, sticky='ew', padx=24)
        operations.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(operations, text='Operation').grid(row=0, column=0, sticky='w')
        for row, (key, (label, _)) in enumerate(OPERATIONS.items(), 1):
            radio = ctk.CTkRadioButton(operations, text=label, variable=self.operation, value=key, command=self.update_controls)
            radio.grid(row=row, column=0, sticky='w', pady=8, padx=(0, 20))
            self.controls.append(radio)
            if key in ('assets', 'board'):
                menu = ctk.CTkOptionMenu(operations, values=['Select a board'], height=32)
                menu.grid(row=row, column=1, sticky='ew')
                self.menus[key] = menu
        actions = ctk.CTkFrame(self, fg_color='transparent')
        actions.grid(row=3, column=0, sticky='ew', padx=24, pady=(14, 12))
        actions.grid_columnconfigure(0, weight=1)
        dry = ctk.CTkCheckBox(actions, text='Dry run', variable=self.dry_run)
        dry.grid(row=0, column=0, sticky='w')
        self.controls.append(dry)
        ctk.CTkButton(actions, text='Clear log', width=90, fg_color='#30363e', command=self.clear_log).grid(row=0, column=1, padx=10)
        self.run_button = ctk.CTkButton(actions, text='Run', width=90, command=self.run)
        self.stop_button = ctk.CTkButton(actions, text='Stop', width=80, fg_color='#704442', command=self.request_stop, state='disabled')
        self.stop_button.grid(row=0, column=2, padx=(0, 10))
        self.run_button.grid(row=0, column=3)
        self.log = ctk.CTkTextbox(self, fg_color='#0c1013', text_color='#abd5b5', border_width=1, border_color='#363e47', font=('Consolas', 12), wrap='word')
        self.log.grid(row=4, column=0, sticky='nsew', padx=24)
        self.log.configure(state='disabled')
        footer = ctk.CTkFrame(self, fg_color='transparent')
        footer.grid(row=5, column=0, sticky='ew', padx=24, pady=(12, 18))
        footer.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(footer, text='Ready', anchor='w', text_color='#aeb8c4')
        self.status.grid(row=0, column=0, sticky='ew')
        self.progress = ctk.CTkProgressBar(footer, height=8)
        self.progress.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        self.progress.set(0)
        self.append_log('[INFO] InvokeAI Database Tools initialized.\n[INFO] Choose the InvokeAI root directory.')
        self.update_controls()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(80, self.poll)

    def append_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text.rstrip() + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def browse(self):
        path = filedialog.askdirectory(title='Select InvokeAI root directory')
        if path:
            self.root_path.set(path)
            self.load_boards()

    def load_boards(self):
        if self.running:
            return
        value = self.root_path.get().strip()
        root = Path(value).expanduser().resolve() if value else None
        if root == self.loaded_root:
            return
        self.boards = []
        self.loaded_root = None
        if root:
            try:
                database, _ = configured_paths(root)
                with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
                    self.boards = [row[0] for row in connection.execute('SELECT board_name FROM boards WHERE board_name IS NOT NULL ORDER BY board_name COLLATE NOCASE')]
                self.loaded_root = root
                self.append_log(f'[INFO] Loaded {len(self.boards)} boards from {database}')
            except Exception as exc:
                self.append_log(f'[ERROR] Unable to load boards: {exc}')
        for menu in self.menus.values():
            previous = menu.get()
            menu.configure(values=self.boards or ['No boards available'])
            menu.set(previous if previous in self.boards else self.boards[0] if self.boards else 'No boards available')
        self.update_controls()

    def update_controls(self):
        for control in self.controls:
            control.configure(state='disabled' if self.running else 'normal')
        for key, menu in self.menus.items():
            menu.configure(state='normal' if not self.running and self.operation.get() == key and self.boards else 'disabled')
        self.run_button.configure(state='disabled' if self.running else 'normal')
        self.stop_button.configure(state='normal' if self.running and not self.stop_pending else 'disabled')

    def run(self):
        if self.running:
            return
        self.load_boards()
        try:
            value = self.root_path.get().strip()
            if not value:
                raise ValueError('Choose the InvokeAI root directory first.')
            root = Path(value).expanduser().resolve()
            if not root.is_dir():
                raise ValueError('The root directory does not exist.')
            database, images = configured_paths(root)
            key = self.operation.get()
            script = REPO / OPERATIONS[key][1]
            if not script.is_file():
                raise ValueError(f'Script not found: {script}')
            if not images.is_dir():
                raise ValueError(f'Images directory not found: {images}')
            args = [sys.executable, '-u', str(script)]
            if key == 'png':
                args += [str(images), '--verbose']
            else:
                if not database.is_file():
                    raise ValueError(f'Database not found: {database}')
                if key == 'restore':
                    args += ['--db-path', str(database), '--outputs-path', str(images)]
                else:
                    if not self.boards or self.loaded_root != root:
                        raise ValueError('Select a root with a readable database and a board.')
                    args += ['--board-name', self.menus[key].get(), '--verbose']
            if self.dry_run.get():
                args.append('--dry-run')
            elif not messagebox.askokcancel('Apply changes', 'Stop InvokeAI before continuing.\nBack up your database and images.\n\nApply this operation now?', parent=self):
                return
        except Exception as exc:
            self.append_log(f'[ERROR] {exc}')
            self.status.configure(text='Check configuration')
            return
        self.stop_directory = tempfile.TemporaryDirectory(prefix='invokeai-tools-')
        self.stop_file = Path(self.stop_directory.name) / 'stop'
        self.stop_pending = False
        self.running = True
        self.update_controls()
        self.status.configure(text='Running — dry run' if self.dry_run.get() else 'Running')
        self.progress.configure(mode='indeterminate')
        self.progress.start()
        self.append_log(f'\n[INFO] {OPERATIONS[key][0]} | {"Dry run" if self.dry_run.get() else "Apply"}')
        threading.Thread(target=self.worker, args=(args, root), daemon=True).start()

    def request_stop(self):
        if not self.running or self.stop_pending:
            return
        try:
            self.stop_file.touch()
        except OSError as exc:
            self.append_log(f'[ERROR] Unable to request stop: {exc}')
            return
        self.stop_pending = True
        self.status.configure(text='Stopping — waiting for a safe point…')
        self.append_log('[INFO] Stop requested. Waiting for the current file or database step to finish.')
        self.update_controls()

    def worker(self, args, root):
        try:
            environment = os.environ.copy()
            environment['PYTHONIOENCODING'] = 'utf-8'
            environment['INVOKEAI_TOOLS_STOP_FILE'] = str(self.stop_file)
            with subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
                for line in process.stdout:
                    self.events.put(('log', line))
                code = process.wait()
            self.events.put(('done', code))
        except Exception as exc:
            self.events.put(('log', f'[ERROR] {exc}'))
            self.events.put(('done', -1))

    def poll(self):
        for _ in range(150):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.append_log(value)
            else:
                self.running = False
                self.progress.stop()
                self.progress.configure(mode='determinate')
                self.progress.set(1 if value == 0 else 0)
                self.status.configure(text='Stopped safely' if value == 130 else 'Completed' if value == 0 else f'Failed (exit {value})')
                self.append_log('[STOPPED] Operation stopped safely.' if value == 130 else '[OK] Operation completed.' if value == 0 else f'[ERROR] Operation failed (exit {value}).')
                self.stop_file = None
                self.stop_pending = False
                self.stop_directory.cleanup()
                self.loaded_root = None
                self.load_boards()
                self.update_controls()
        self.after(80, self.poll)

    def close(self):
        if self.running:
            messagebox.showinfo('Operation running', 'Wait for the operation to finish before closing.', parent=self)
            return
        self.destroy()


if __name__ == '__main__':
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    App().mainloop()


