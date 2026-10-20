# Typical RouterOS script: alert when a watched MAC gets DHCP deassigned (offline).
# Wire to Logging → topics=dhcp, Action=script (filter/prefix: deassigned).
# Log example:
#   dhcp1 deassigned 192.168.1.100 for A1:B2:C3:D4:E5:F6 GFY-LX1
# Put MACs exactly as in the log (usually uppercase). Array separator is ; only.

:local hookBase "http://xxx:8443"
:local hookSecret "xxx"
:local deviceId "xxx"
:local eventName "Device offline"
:local watchMacs {"A1:B2:C3:D4:E5:F6";"AA:BB:CC:DD:EE:FF"}
:local watchNames {"GFY-LX1";"Other device"}

:local rawMsg ""
:do { :set rawMsg $"message" } on-error={}
:if ([:len $rawMsg] = 0) do={ :do { :set rawMsg $message } on-error={} }
:if ([:len $rawMsg] = 0) do={
  :local rows [/log print as-value where message~" deassigned "]
  :if ([:len $rows] > 0) do={
    :set rawMsg ($rows->([:len $rows] - 1)->"message")
  }
}
:if ([:len $rawMsg] = 0) do={ :return "" }
:if ([:typeof [:find $rawMsg " deassigned "]] != "num") do={ :return "" }

:local pVerb [:find $rawMsg " deassigned "]
:local pSep [:find $rawMsg " for "]
:if ([:typeof $pSep] != "num") do={ :return "" }

:local ipAddr [:pick $rawMsg ($pVerb + 12) $pSep]
:local rest [:pick $rawMsg ($pSep + 5) [:len $rawMsg]]
:if ([:len $rest] < 17) do={ :return "" }
:local mac [:pick $rest 0 17]
:local dhcpHost ""
:if ([:len $rest] > 18) do={
  :set dhcpHost [:pick $rest 18 [:len $rest]]
}

:local matched false
:local friendly ""
:local idx 0
:foreach m in=$watchMacs do={
  :if ($m = $mac) do={
    :set matched true
    :if ([:len $watchNames] > $idx) do={
      :set friendly ($watchNames->$idx)
    }
  }
  :set idx ($idx + 1)
}
:if ($matched = false) do={ :return "" }

:local label $mac
:if ([:len $friendly] > 0) do={ :set label $friendly } else={
  :if ([:len $dhcpHost] > 0) do={ :set label $dhcpHost }
}

:local ident [/system identity get name]
:local board [/system resource get board-name]
:local ver [/system resource get version]
:local q "`"
:local niceMsg ("OFFLINE" \
  . " | router=" . $q . $ident . $q \
  . " | board=" . $q . $board . $q \
  . " | ros=" . $q . $ver . $q \
  . " | device=" . $q . $label . $q \
  . " | mac=" . $q . $mac . $q \
  . " | ip=" . $q . $ipAddr . $q)
:if ([:len $dhcpHost] > 0) do={
  :set niceMsg ($niceMsg . " | dhcp-host=" . $q . $dhcpHost . $q)
}

:local url ($hookBase . "/hooks/v1/event" \
  . "?device_id=" . [:convert $deviceId to=url] \
  . "&secret=" . [:convert $hookSecret to=url] \
  . "&event=" . [:convert $eventName to=url] \
  . "&severity=warn" \
  . "&message=" . [:convert $niceMsg to=url])
/tool fetch url=$url http-method=get keep-result=no check-certificate=no
