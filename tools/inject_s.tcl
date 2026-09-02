init
halt
# 注入指令: cmd_buf[0]='S', head=1, tail=0
mww 0x20000780 0x53
mwb 0x20000788 1
mwb 0x20000789 0
echo {注入 S 指令完成, 放跑 1.5 秒 ...}
resume
sleep 1500
halt
echo {=== 1.5 秒后 ===}
echo SAMPLE=[mdw 0x20003860]
echo M_COUNT=[mdw 0x20003864]
echo N_COUNT=[mdw 0x20003868]
echo GPIOB_ODR=[mdw 0x48000414]
resume
shutdown
