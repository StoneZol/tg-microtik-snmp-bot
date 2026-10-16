# Typical RouterOS script: alert on failed login attempts
# (unauthorized probes / brute-force / wrong password).
# Wire to Logging → Action=script; parses "login failure"
# and GETs /hooks/v1/event on the bot (set device_id + secret below).


:local hookBase "http://xxx:8443"
:local hookSecret "xxx"
:local deviceId "xxx"
:local rawMsg ""
:do { :set rawMsg $"message" } on-error={}
:if ([:len $rawMsg] = 0) do={ :do { :set rawMsg $message } on-error={} }
:if ([:len $rawMsg] = 0) do={
  :local rows [/log print as-value where message~"login failure"]
  :if ([:len $rows] > 0) do={
    :set rawMsg ($rows->([:len $rows] - 1)->"message")
  }
}
:if ([:len $rawMsg] = 0) do={ :set rawMsg "login failure" }
:if ([:typeof [:find $rawMsg "login failure"]] != "num") do={ :return "" }
:local username "?"
:local srcAddress "?"
:local service "?"
:local pUser [:find $rawMsg "for user "]
:local pFrom [:find $rawMsg " from "]
:local pVia [:find $rawMsg " via "]
:if ([:typeof $pUser] = "num" && [:typeof $pFrom] = "num") do={
  :set username [:pick $rawMsg ($pUser + 9) $pFrom]
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
:local niceMsg ("FAILED LOGIN" \
  . " | router=" . $ident \
  . " | board=" . $board \
  . " | ros=" . $ver \
  . " | user=" . $username \
  . " | from=" . $srcAddress \
  . " | via=" . $service \
  . " | raw=" . $rawMsg)
:local url ($hookBase . "/hooks/v1/event" \
  . "?device_id=" . [:convert $deviceId to=url] \
  . "&secret=" . [:convert $hookSecret to=url] \
  . "&event=login_failure" \
  . "&severity=critical" \
  . "&message=" . [:convert $niceMsg to=url])
/tool fetch url=$url http-method=get keep-result=no check-certificate=no