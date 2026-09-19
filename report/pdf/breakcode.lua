-- Let long inline code (request strings in the compatibility tables) wrap inside a table cell.
-- Pandoc renders `code` as \texttt{...}, which has no break points in a URL-like string, so those
-- cells run into the margin. Rewrite long spans to raw LaTeX with \allowbreak after punctuation.

local ESCAPE = {
  ["\\"] = "\\textbackslash{}", ["{"] = "\\{", ["}"] = "\\}", ["$"] = "\\$",
  ["&"] = "\\&", ["#"] = "\\#", ["_"] = "\\_", ["%"] = "\\%",
  ["^"] = "\\textasciicircum{}", ["~"] = "\\textasciitilde{}",
}

local MIN_LENGTH = 28 -- shorter spans always fit; leave them as ordinary code

function Code(el)
  if utf8.len(el.text) == nil or utf8.len(el.text) < MIN_LENGTH then
    return nil
  end
  local out = {}
  for _, cp in utf8.codes(el.text) do
    local ch = utf8.char(cp)
    out[#out + 1] = ESCAPE[ch] or ch
    if ch:match("[/%?&=%.,_%-:;]") then
      out[#out + 1] = "\\allowbreak{}"
    end
  end
  return pandoc.RawInline("latex", "\\texttt{" .. table.concat(out) .. "}")
end
