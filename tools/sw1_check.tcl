init
halt
# PH3(BOOT0) 设为数字输入
mww 0x48001C00 0x0000000F
# PB4 设为数字输入 (保留 GPIOB 其它位)
set m [lindex [mdw 0x48000400] 1]
set mv [format 0x%x [expr "0x$m" & ~0x300]]
mww 0x48000400 $mv
echo {=== GPIOB MODER after fix ===}
echo [mdw 0x48000400]
echo {=== poll start: PH3(BOOT0) / PB4(刷新), 按住按键观察变化 ===}
for {set i 0} {$i < 750} {incr i} {
    set a [catch {mdw 0x48001C14} av]
    set b [catch {mdw 0x48000414} bv]
    if {$a || $b} {
        echo {>>> RESET EVENT (复位按键被按下) <<<}
        catch {halt}
        # 复位后 GPIO 回到默认(模拟), 重新配置
        mww 0x48001C00 0x0000000F
        set m [lindex [mdw 0x48000400] 1]
        set mv [format 0x%x [expr "0x$m" & ~0x300]]
        mww 0x48000400 $mv
    } else {
        echo "PH3=[lindex $av 1]  PB4=[lindex $bv 1]"
    }
    sleep 400
}
resume
shutdown
