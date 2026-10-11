from pathlib import Path
from contextlib import closing
import codecs
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import tempfile
import runpy
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yaml
from version import VERSION

REPO = Path(__file__).resolve().parent
OPERATIONS = {
    'restore': ('Restore Images Database', 'scripts/Restore_Images_DB_v3.1.py'),
    'assets': ('Convert Board to Assets', 'scripts/Convert_Board_to_Assets_v1.1.py'),
    'board': ('Convert Assets to Board', 'scripts/Convert_Assets_to_Board_v1.1.py'),
    'png': ('Recompress PNG', 'scripts/png_recompress_level9_v1.0.py'),
    'repair': ('Fix Image Deletion (#9504)', 'scripts/Repair_Image_Deletion.py'),
}



OPERATION_HELP = {
    'repair': (
        "Repair the database issue that prevents deleting images after a folder "
        "migration (InvokeAI #9504). Adds cascading deletion to the move journal.\n\n"
        "Stop InvokeAI before applying. A database backup is created automatically. "
        "Images, files and completed move records are preserved. Unfinished migrations "
        "or database integrity errors block the repair.\n\n"
        "Dry run checks whether the repair is needed without writing changes. "
        "Already repaired databases are left unchanged. Stop rolls back pending "
        "schema changes; a final commit already in progress is allowed to finish."
    ),
    'restore': (
        "Register missing PNG, JPEG and WebP images and recover embedded metadata. "
        "Scans subfolders and creates missing WebP thumbnails.\n\n"
        "Source folder is optional: files inside outputs/images stay in place; "
        "external files are copied and verified, leaving originals untouched. "
        "Conflicting files are skipped.\n\n"
        "Import board applies to newly registered images only. Leave it blank for "
        "automatic daily boards. Existing images keep their board assignments.\n\n"
        "Dry run previews changes. Stop rolls back database changes; completed "
        "copies remain available for the next run."
    ),
    'assets': (
        "Reclassify images on the selected board as user assets. "
        "They become available in InvokeAI's asset views.\n\n"
        "Board membership, metadata and image files stay unchanged. "
        "Images whose files cannot be found are skipped.\n\n"
        "Dry run lists planned changes. Stop rolls back the pending database "
        "transaction; a final commit already in progress is allowed to finish."
    ),
    'board': (
        "Reclassify images on the selected board as regular gallery images.\n\n"
        "Images remain on the same board; files are not moved or copied. "
        "Metadata is preserved. Images whose files cannot be found are skipped.\n\n"
        "Dry run lists planned changes. Stop rolls back the pending database "
        "transaction; a final commit already in progress is allowed to finish."
    ),
    'png': (
        "Recompress PNG files in the configured outputs/images folder, including "
        "subfolders, to reduce disk usage without changing pixels. "
        "Uses Pillow compression level 9 and preserves supported InvokeAI metadata.\n\n"
        "Original files are replaced only when the result is smaller. "
        "JPEG and WebP files are not processed.\n\n"
        "Dry run performs the calculation without replacing files. Stop finishes "
        "the current file safely; earlier completed files remain recompressed."
    ),
}


def monitor_work_area(widget):
    """Find the work area around the widget, including negative monitor origins."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                        ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        point = wintypes.POINT(widget.winfo_rootx() + widget.winfo_width() // 2,
                              widget.winfo_rooty() + widget.winfo_height() // 2)
        monitor = user32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return rect.left, rect.top, rect.right, rect.bottom
    x, y = widget.winfo_vrootx(), widget.winfo_vrooty()
    return x, y, x + widget.winfo_vrootwidth(), y + widget.winfo_vrootheight()


def tooltip_position(anchor, size, bounds):
    """Place below the widget, or above it when close to the monitor's bottom."""
    anchor_x, anchor_y, anchor_height = anchor
    width, height = size
    left, top, right, bottom = bounds
    x = max(left + 8, min(anchor_x + 20, right - width - 8))
    y = anchor_y + anchor_height + 8
    if y + height > bottom - 8:
        y = anchor_y - height - 8
    y = max(top + 8, min(y, bottom - height - 8))
    return x, y


class HoverHelp:
    """Delayed tooltip, with no additional GUI dependency."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.pending = None
        self.window = None
        widget.bind('<Enter>', self.schedule, add=True)
        widget.bind('<Leave>', self.hide, add=True)
        widget.bind('<ButtonPress>', self.hide, add=True)
        widget.bind('<Destroy>', self.hide, add=True)

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(550, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists():
            return
        window = tk.Toplevel(self.widget)
        self.window = window
        window.withdraw()
        window.transient(self.widget.winfo_toplevel())
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        tk.Label(window, text=self.text, justify='left', anchor='w',
                 wraplength=410, font=('Segoe UI', 11), bg='#252c34',
                 fg='#e3e8ef', padx=14, pady=12, relief='solid', bd=1).pack()
        window.update_idletasks()
        x, y = tooltip_position(
            (self.widget.winfo_rootx(), self.widget.winfo_rooty(), self.widget.winfo_height()),
            (window.winfo_reqwidth(), window.winfo_reqheight()),
            monitor_work_area(self.widget),
        )
        # A leading '+' preserves absolute coordinates even when x/y are negative.
        window.geometry(f'+{x}+{y}')
        window.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None

def configured_paths(root):
    file = root / 'invokeai.yaml'
    config = (yaml.safe_load(file.read_text(encoding='utf-8-sig')) or {}) if file.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError('Invalid invokeai.yaml: expected a mapping')
    invokeai = config.get('InvokeAI') or {}
    if not isinstance(invokeai, dict):
        raise ValueError('Invalid invokeai.yaml: InvokeAI must be a mapping')
    legacy = invokeai.get('Paths') or {}
    if not isinstance(legacy, dict):
        raise ValueError('Invalid invokeai.yaml: InvokeAI.Paths must be a mapping')

    def directory(keys, default):
        value = next((mapping[key] for mapping in (legacy, config) for key in keys
                      if mapping.get(key) is not None), default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Invalid path setting: {keys[0]}')
        path = Path(value).expanduser()
        return path if path.is_absolute() else root / path

    return directory(('db_dir',), 'databases') / 'invokeai.db', directory(('outdir', 'outputs_dir'), 'outputs') / 'images'


def parse_progress_line(line):
    if not line.startswith('[PROGRESS] '):
        return None
    try:
        processed, total = map(int, line[len('[PROGRESS] '):].split())
    except ValueError:
        return None
    return (processed, total) if 0 <= processed <= total else None


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f'InvokeAI Database Tools v{VERSION}')
        self.geometry('760x790')
        self.minsize(680, 720)
        self.configure(fg_color='#181c20')
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self.root_path = tk.StringVar()
        self.source_path = tk.StringVar()
        self.import_board = tk.StringVar()
        self.restore_controls = []
        self.operation = tk.StringVar(value='restore')
        self.dry_run = tk.BooleanVar(value=True)
        self.events = queue.Queue()
        self.running = False
        self.progress_count = None
        self.stop_file = None
        self.stop_pending = False
        self.boards = []
        self.loaded_root = None
        self.controls = []
        self.selected_boards = {'assets': None, 'board': None}
        self.board_selection_root = None
        self.menus = {}
        self.operation_help = []
        ctk.CTkLabel(self, text=f'InvokeAI Database Tools v{VERSION}', font=('Segoe UI', 18, 'bold'), anchor='w').grid(row=0, column=0, sticky='ew', padx=24, pady=(20, 16))
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
            self.operation_help.append(HoverHelp(radio, OPERATION_HELP[key]))
            if key in ('assets', 'board'):
                menu = ctk.CTkOptionMenu(operations, values=['Select a board'], height=32,
                                         command=lambda value, operation=key: self.select_board(operation, value))
                menu.grid(row=row, column=1, sticky='ew')
                self.menus[key] = menu
        ctk.CTkLabel(operations, text='Source folder (optional)').grid(row=6, column=0, sticky='w')
        source_frame = ctk.CTkFrame(operations, fg_color='transparent')
        source_frame.grid(row=6, column=1, sticky='ew', pady=(8, 4))
        source_frame.grid_columnconfigure(0, weight=1)
        source_entry = ctk.CTkEntry(source_frame, textvariable=self.source_path, placeholder_text='Default: outputs/images')
        source_entry.grid(row=0, column=0, sticky='ew')
        source_browse = ctk.CTkButton(source_frame, text='Browse', width=70, command=self.browse_source)
        source_browse.grid(row=0, column=1, padx=(6, 0))
        ctk.CTkLabel(operations, text='Import board (optional)').grid(row=7, column=0, sticky='w')
        board_entry = ctk.CTkEntry(operations, textvariable=self.import_board, placeholder_text='Default: automatic boards')
        board_entry.grid(row=7, column=1, sticky='ew', pady=(4, 8))
        self.restore_controls.extend([source_entry, source_browse, board_entry])
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
        self.log_menu = tk.Menu(self, tearoff=False, bg='#252c34', fg='#e3e8ef',
                                activebackground='#1f6aa5', activeforeground='white')
        self.log_menu.add_command(label='Copy', accelerator='Ctrl+C', command=self.copy_log_selection)
        self.log_menu.add_command(label='Copy all', command=self.copy_log_all)
        self.log.bind('<Button-3>', self.show_log_menu)
        self.log.bind('<Control-c>', self.copy_log_selection)
        self.log.bind('<Control-a>', self.select_all_log)
        footer = ctk.CTkFrame(self, fg_color='transparent')
        footer.grid(row=5, column=0, sticky='ew', padx=24, pady=(12, 18))
        footer.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(footer, text='Ready', anchor='w', text_color='#aeb8c4')
        self.status.grid(row=0, column=0, sticky='ew')
        self.progress = ctk.CTkProgressBar(footer, height=8)
        self.progress.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        self.progress.set(0)
        self.append_log(f'[INFO] InvokeAI Database Tools v{VERSION} initialized.\n[INFO] Choose the InvokeAI root directory.')
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

    def show_log_menu(self, event):
        self.log_menu.entryconfigure(0, state='normal' if self.log.tag_ranges('sel') else 'disabled')
        try:
            self.log_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.log_menu.grab_release()
        return 'break'

    def copy_log_selection(self, event=None):
        if self.log.tag_ranges('sel'):
            self.clipboard_clear()
            self.clipboard_append(self.log.get('sel.first', 'sel.last'))
        return 'break'

    def copy_log_all(self):
        text = self.log.get('1.0', 'end-1c')
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def select_all_log(self, event=None):
        self.log.focus_set()
        self.log.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def browse_source(self):
        path = filedialog.askdirectory(parent=self, title='Select image batch folder')
        if path:
            self.source_path.set(path)

    def browse(self):
        path = filedialog.askdirectory(parent=self, title='Select InvokeAI root directory')
        if path:
            self.root_path.set(path)
            self.load_boards()

    def load_boards(self):
        if self.running:
            return
        value = self.root_path.get().strip()
        try:
            root = Path(value).expanduser().resolve() if value else None
        except (OSError, ValueError) as exc:
            self.append_log(f'[ERROR] Invalid root directory: {exc}')
            root = None
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
        if root != self.board_selection_root:
            self.selected_boards = {'assets': None, 'board': None}
        self.board_selection_root = root
        for key, menu in self.menus.items():
            selected = self.selected_boards[key]
            if selected not in self.boards:
                selected = None
                self.selected_boards[key] = None
            menu.configure(values=self.boards or ['No boards available'])
            menu.set(selected if selected is not None else 'Select a board')
        self.update_controls()

    def select_board(self, operation, value):
        self.selected_boards[operation] = value if value in self.boards else None
        self.update_controls()

    def update_controls(self):
        for control in self.controls:
            control.configure(state='disabled' if self.running else 'normal')
        for control in self.restore_controls:
            control.configure(state='normal' if not self.running and self.operation.get() == 'restore' else 'disabled')
        for key, menu in self.menus.items():
            enabled = not self.running and self.operation.get() == key and bool(self.boards)
            menu.configure(
                state='normal' if enabled else 'disabled',
                fg_color='#1f6aa5' if enabled else '#303438',
                button_color='#144870' if enabled else '#303438',
                button_hover_color='#36719f' if enabled else '#303438',
                text_color_disabled='#858b92',
            )
        operation = self.operation.get()
        needs_board = operation in self.menus
        can_run = not self.running and (not needs_board or self.selected_boards[operation] in self.boards)
        self.run_button.configure(state='normal' if can_run else 'disabled')
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
            if key != 'repair' and not images.is_dir():
                raise ValueError(f'Images directory not found: {images}')
            args = ([sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-u', str(Path(__file__).resolve())]) + ['--run-operation', key]
            if key == 'png':
                args += [str(images), '--verbose']
            else:
                if not database.is_file():
                    raise ValueError(f'Database not found: {database}')
                if key == 'repair':
                    args += ['--db-path', str(database)]
                elif key == 'restore':
                    args += ['--db-path', str(database), '--outputs-path', str(images)]
                    if self.source_path.get().strip():
                        args += ['--source-path', self.source_path.get().strip()]
                    if self.import_board.get().strip():
                        args += ['--board-name', self.import_board.get().strip()]
                else:
                    if not self.boards or self.loaded_root != root:
                        raise ValueError('Select a root with a readable database and a board.')
                    selected_board = self.selected_boards[key]
                    if selected_board is None or selected_board not in self.boards:
                        raise ValueError('Select a board before running the conversion.')
                    args += ['--board-name', selected_board, '--verbose']
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
        self.progress_count = None
        self.status.configure(text='Running — dry run' if self.dry_run.get() else 'Running')
        self.progress.configure(mode='indeterminate')
        self.progress.set(0)
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
        self.status.configure(text=self.progress_status('Stopping — waiting for a safe point…'))
        self.append_log('[INFO] Stop requested. Waiting for the current file or database step to finish.')
        self.update_controls()

    def progress_status(self, label):
        if self.progress_count is None:
            return label
        processed, total = self.progress_count
        return f'{label} [{processed} of {total} processed]'

    def update_progress(self, counts):
        self.progress_count = counts
        processed, total = counts
        self.progress.stop()
        self.progress.configure(mode='determinate')
        self.progress.set(processed / total if total else 0)
        label = ('Stopping — waiting for a safe point…' if self.stop_pending else
                 'Running — dry run' if self.dry_run.get() else 'Running')
        self.status.configure(text=self.progress_status(label))

    def worker(self, args, root):
        try:
            environment = os.environ.copy()
            environment['PYTHONIOENCODING'] = 'utf-8'
            environment['INVOKEAI_TOOLS_PROGRESS'] = '1'
            environment['INVOKEAI_TOOLS_STOP_FILE'] = str(self.stop_file)
            log_path = self.stop_file.parent / 'worker.log'
            log_path.touch()
            # Explicit file logging also works in a windowed EXE with no stdout handles.
            command = args[:]
            position = command.index('--run-operation') + 2
            command[position:position] = ['--worker-log', str(log_path)]
            pending = ''
            def consume(chunk, final=False):
                nonlocal pending
                pending += chunk
                lines = pending.split('\n')
                pending = lines.pop()
                if final and pending:
                    lines.append(pending)
                    pending = ''
                messages = []
                latest_progress = None
                for line in lines:
                    counts = parse_progress_line(line)
                    if counts is None:
                        messages.append(line)
                    else:
                        latest_progress = counts
                if messages:
                    self.events.put(('log', '\n'.join(messages)))
                if latest_progress is not None:
                    self.events.put(('progress', latest_progress))

            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            with log_path.open('rb') as output:
                with subprocess.Popen(command, cwd=root, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, env=environment,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
                    while True:
                        chunk = output.read()
                        consume(decoder.decode(chunk))
                        if process.poll() is not None:
                            remainder = output.read()
                            consume(decoder.decode(remainder, final=True), final=True)
                            code = process.returncode
                            break
                        time.sleep(0.05)
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
            elif kind == 'progress':
                self.update_progress(value)
            else:
                self.running = False
                self.progress.stop()
                self.progress.configure(mode='determinate')
                if value == 0 or self.progress_count is None:
                    self.progress.set(1 if value == 0 else 0)
                label = 'Stopped safely' if value == 130 else 'Completed' if value == 0 else f'Failed (exit {value})'
                self.status.configure(text=self.progress_status(label))
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


def run_embedded_operation(arguments):
    key = arguments[0] if arguments else ''
    if key not in OPERATIONS:
        return 2
    remaining = arguments[1:]
    if len(remaining) < 2 or remaining[0] != '--worker-log':
        return 2
    log_path = remaining[1]
    with open(log_path, 'w', encoding='utf-8', buffering=1) as output:
        sys.stdout = output
        sys.stderr = output
        try:
            script = REPO / OPERATIONS[key][1]
            sys.argv = [str(script), *remaining[2:]]
            runpy.run_path(str(script), run_name='__main__')
            return 0
        except SystemExit as exc:
            if exc.code is None:
                return 0
            if isinstance(exc.code, int):
                return exc.code
            print(exc.code)
            return 1
        except BaseException:
            traceback.print_exc()
            return 1
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--run-operation':
        raise SystemExit(run_embedded_operation(sys.argv[2:]))
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    app = App()
    if '--check-gui' in sys.argv[1:]:
        app.update()
        app.destroy()
    else:
        app.mainloop()


