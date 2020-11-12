#!/bin/bash
# docker run --entrypoint /bin/bash -v $PWD:/ioc -it pcdshub/ads-ioc:latest -c 'cd /ioc && $ADS_IOC_PATH/bin/rhel7-x86_64/adsIoc st.cmd'
/reg/g/pcds/epics/ioc/common/ads-ioc/R0.2.4/bin/rhel7-x86_64/adsIoc st.cmd
