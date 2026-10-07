# Review checklist

Used when a big change is reviewed in Claude Code (for example `/code-review` on a PR).
CI already covers syntax, loader names, SQL apply/re-apply, and luacheck, so a review
should spend its effort on bugs a compiler can't catch.

Look hardest for:
- **Item/gold duplication and exploits.** Async DB writes (CharacterDatabase.Execute) racing
  synchronous reads; items still in an open trade window or mail; two requests in the same
  frame; client-supplied counts, GUIDs or slots used without validation; GM-only commands
  with unbounded arguments.
- **Bots.** mod-playerbots bots are sessions without a socket. Use WorldSession::IsHeadless()
  (IsBot() is gone in this core). Other players' alt bots are headless too, so only touch
  random bots or the acting player's own bots.
- **World-thread stalls.** Synchronous queries (Query/PQuery) in hooks that fire often
  (login, every update, every loot), and full-table scans without a rate limit.
- **Individual progression.** The player's era is quest 66000+n. Content must not leak past
  a character's tier (TBC/WotLK items, recipes, trainers, mounts, riding).
- **SQL.** Must be re-runnable: the server re-applies a file whenever it changes, so use
  DELETE before INSERT or REPLACE. creature/gameobject use `id`, not `id1`. Uninstall SQL
  belongs in data/sql/uninstall/. Use custom entry ranges, never stock IDs.
- **Addons (Lua 5.1, Interface 30300).** Leaked globals, taint from secure frames in combat,
  frames created repeatedly, events never unregistered, and DragonUI being optional.
- **Lifetime.** Raw Player*/Creature* pointers kept past the current call (store the
  ObjectGuid instead); events and timers that outlive the map or player.
