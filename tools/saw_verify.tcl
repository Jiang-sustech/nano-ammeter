init
halt
echo {=== 第一拍 (刚 halt) ===}
echo SAMPLE1=[mdw 0x200032C8]
echo M1=[mdw 0x200032CC]
echo N1=[mdw 0x200032D0]
echo SEL1=[mdw 0x200032D4]
resume
sleep 500
halt
echo {=== 第二拍 (500ms 后) ===}
echo SAMPLE2=[mdw 0x200032C8]
echo M2=[mdw 0x200032CC]
echo N2=[mdw 0x200032D0]
echo SEL2=[mdw 0x200032D4]
dump_image C:/Users/40512/STM32Toolchain/saw_uart.bin 0x200001F4 0x30D4
echo {=== dump done ===}
resume
shutdown
