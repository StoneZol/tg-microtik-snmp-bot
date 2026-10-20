# Typical RouterOS script: alert on successful login
# (who signed in, from where, via which service).
# Wire to Logging → Action=script; parses "logged in"
# and GETs /hooks/v1/event on the bot (set device_id + secret below).

:local hookBase "http://xxx:8443"
:local hookSecret "xxx"
:local deviceId "xxx"
# Free-form label shown as event: in Telegram — pick any name you like
:local eventName "Successful login"
:local rawMsg ""
:do { :set rawMsg $"message" } on-error={}
:if ([:len $rawMsg] = 0) do={ :do { :set rawMsg $message } on-error={} }
:if ([:len $rawMsg] = 0) do={
  :local rows [/log print as-value where message~"logged in"]
  :if ([:len $rows] > 0) do={
    :set rawMsg ($rows->([:len $rows] - 1)->"message")
  }
}
:if ([:len $rawMsg] = 0) do={ :set rawMsg "logged in" }
:if ([:typeof [:find $rawMsg "logged in"]] != "num") do={ :return ""}
:local username "?"
:local srcAddress "?"
:local service "?"
:local pUser [:find $rawMsg "user "]
:local pLogged [:find $rawMsg " logged in"]
:local pFrom [:find $rawMsg " from "]
:local pVia [:find $rawMsg " via "]
:if ([:typeof $pUser] = "num" && [:typeof $pLogged] = "num") do={
  :set username [:pick $rawMsg ($pUser + 5) $pLogged]
}
:if ([:typeof $pFrom] = "num") do={
  :if ([:typeof $pVia] = "num") do={
    :set srcAddress [:pick $rawMsg ($pFrom + 6) $pVia]
    :set service [:pick $rawMsg ($pVia + 5) [:len $rawMsg]]
  } else={
    :set srcAddress [:pick $rawMsg ($pFrom + 6) [:len $rawMsg]]
  }
}
:local ident [/system identity get name]
:local board [/system resource get board-name]
:local ver [/system resource get version]
# Backticks → Telegram Markdown tap-to-copy (bot sends parse_mode=Markdown)
:local q "`"
:local niceMsg ("SUCCESSFUL LOGIN" \
  . " | router=" . $ident \
  . " | board=" . $board \
  . " | ros=" . $ver \
  . " | user=" . $q . $username . $q \
  . " | from=" . $q . $srcAddress . $q \
  . " | via=" . $q . $service . $q)
  # . " | raw=" . $rawMsg)
:local url ($hookBase . "/hooks/v1/event" \
  . "?device_id=" . [:convert $deviceId to=url] \
  . "&secret=" . [:convert $hookSecret to=url] \
  . "&event=" . [:convert $eventName to=url] \
  . "&severity=info" \
  . "&message=" . [:convert $niceMsg to=url])
/tool fetch url=$url http-method=get keep-result=no check-certificate=no
