docker run --entrypoint /bin/bash -v $PWD:/ioc -it pcdshub/ads-ioc:latest -c 'cd /ioc && $ADS_IOC_PATH/bin/rhel7-x86_64/adsIoc st.cmd'
