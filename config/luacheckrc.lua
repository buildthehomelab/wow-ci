-- luacheck config for WoW 3.3.5a (Lua 5.1) addons.
--
-- The WoW API has thousands of globals, so instead of listing them all:
--   * PascalCase / UPPER_CASE globals (GetItemInfo, UIParent, SLASH_FOO1, SavedVariables)
--     are allowed. They are the API, frame names, and saved variables.
--   * lower-case globals must be listed below. An unknown lower-case global is almost
--     always a typo or a missing `local`, so CI reports it as an error.
-- A repo can extend this with its own .luacheckrc (it is merged on top).
std = "lua51"
max_line_length = false
codes = true

-- Lower-case globals the 3.3.5 client provides on top of Lua 5.1.
read_globals = {
    -- string aliases
    "format", "gsub", "strbyte", "strchar", "strfind", "strjoin", "strlen", "strlower",
    "strmatch", "strrep", "strrev", "strsplit", "strsub", "strtrim", "strupper", "strconcat",
    "strlenutf8", "gmatch", "gfind", "tostringall",
    -- table aliases
    "tinsert", "tremove", "wipe", "sort", "getn", "foreach", "foreachi", "tContains", "tDeleteItem",
    -- math aliases
    "abs", "ceil", "floor", "max", "min", "mod", "sqrt", "random", "fmod", "exp", "log",
    "log10", "frexp", "ldexp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "deg", "rad", "PI",
    -- misc client functions
    "bit", "date", "time", "difftime", "debugstack", "debuglocals", "debugprofilestart",
    "debugprofilestop", "geterrorhandler", "seterrorhandler", "hooksecurefunc",
    "issecure", "issecurevariable", "securecall", "forceinsecure", "getglobal", "setglobal",
    "message", "gcinfo", "scrub", "loadstring", "newproxy",
    -- implicit globals in old-style XML scripts
    "this", "arg1", "arg2", "arg3", "arg4", "arg5", "arg6", "arg7", "arg8", "arg9",
    "event",
    string = { other_fields = true, fields = {
        "trim", "split", "join", "rtgsub", "utf8len", "utf8sub",
    } },
    table = { other_fields = true, fields = { "wipe" } },
}

ignore = {
    "212",          -- unused argument: event handlers take (self, event, ...) by convention
    "213",          -- unused loop variable (for _, v in pairs ...)
    "542",          -- empty if branch
    "631",          -- line too long
    "11./^[A-Z]",   -- PascalCase / UPPER_CASE globals: WoW API, frames, saved variables
    "11./^_[A-Z]",  -- _G-style names like _ADDON
}

exclude_files = {
    ".git/**",
    ".claude/**",
    "**/Libs/**", "**/libs/**",   -- vendored libraries (Ace, LibStub, ...) aren't ours to lint
}
