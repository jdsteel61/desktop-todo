# Contributing

Thanks for taking a look. desktop-todo is deliberately small, and a few ground rules keep it that way:

- **One file, standard library only.** The whole app is `todo.pyw`, with nothing to install.
  Changes that add a dependency or split the file are unlikely to be merged.
- **Windows first.** It uses the Win32 API for the borderless window, the single-instance check and start at login.
- **Keyboard first, small UI.** A new feature should fit into a key or a setting, not a new window.
- **Old data files keep working.** Add new fields with a default in `check()`, so existing `data.json` files still load.

## Making a change

1. For anything bigger than a bug fix, open an issue first so we can agree on the idea before you spend time on it.
2. Fork the repo and make your change on a branch.
3. Run the checks (CI uses ruff 0.15.6):

   ```
   python -m unittest discover -s tests -v
   ruff check .
   ```

4. Try it by hand against a scratch data file, so your own list isn't touched. In PowerShell:

   ```
   $env:TODO_DATA = "$env:TEMP\todo-test.json"; pythonw todo.pyw
   ```

5. Open a pull request that says what changed and why. If you touched parsing, scheduling or loading, add a test.
