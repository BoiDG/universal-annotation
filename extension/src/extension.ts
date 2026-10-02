import * as net from 'node:net';
import * as vscode from 'vscode';

const PIPE = '\\\\.\\pipe\\prompt-scratchpad-v1';

type Location = {
  version: 1;
  type: 'selection';
  active: true;
  application: string;
  file_path: string;
  start_line: number;
  end_line: number;
  workspace: string;
};

function send(message: Location | {version: 1; type: 'selection'; active: false}): void {
  const socket = net.createConnection(PIPE);
  socket.setTimeout(1500, () => socket.destroy());
  socket.on('error', () => { /* The scratchpad may be closed; no prompt content is sent. */ });
  socket.on('data', () => { /* Drain the local acknowledgement. */ });
  socket.end(JSON.stringify(message) + '\n');
}

function publishLocation(): void {
  const editor = vscode.window.activeTextEditor;
  if (!vscode.window.state.focused || !editor || editor.document.uri.scheme !== 'file') {
    send({version: 1, type: 'selection', active: false});
    return;
  }
  const selections = editor.selections;
  if (!selections.length) return;
  const first = Math.min(...selections.map(selection => selection.start.line));
  const last = Math.max(...selections.map(selection =>
    !selection.isEmpty && selection.end.character === 0 && selection.end.line > selection.start.line
      ? selection.end.line - 1 : selection.end.line));
  send({
    version: 1,
    type: 'selection',
    active: true,
    application: vscode.env.appName,
    file_path: editor.document.uri.fsPath,
    start_line: first + 1,
    end_line: Math.max(first, last) + 1,
    workspace: vscode.workspace.name || ''
  });
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.window.onDidChangeTextEditorSelection(publishLocation),
    vscode.window.onDidChangeActiveTextEditor(publishLocation),
    vscode.window.onDidChangeWindowState(publishLocation),
    vscode.commands.registerCommand('promptScratchpad.refreshLocation', publishLocation)
  );
  publishLocation();
}

export function deactivate(): void {
  send({version: 1, type: 'selection', active: false});
}
