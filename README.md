# CloudPRNT

A CloudPRNT app for Frappe, running on Ubuntu with PHP 8.3.

## Prerequisites

- Frappe Framework  
- Ubuntu (22.04+ recommended)  
- PHP 8.3 with PHP-FPM

## Installation

1. Clone the app into your Bench:
   ```bash
   cd ~/frappe-bench/apps
   bench get-app https://github.com/bvisible/CloudPRNT.git
   ```

2. Install the app on your site:
   ```bash
   bench --site your-site install-app cloudprnt
   ```

3. Make the cputil utility executable:
   ```bash
   chmod +x apps/cloudprnt/cloudprnt/cputil/cputil
   ```

## Nginx Configuration

Add the following to your server { … } block:

```nginx
location /cloudprnt {
    alias /home/frappe/frappe-bench/apps/cloudprnt/cloudprnt/cloudprnt/php;
    index index.php index.html index.htm;

    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;
        
        # Essential FastCGI parameters
        fastcgi_param SCRIPT_FILENAME $request_filename;
        fastcgi_param SCRIPT_NAME     $fastcgi_script_name;

        # FastCGI debugging
        fastcgi_param REQUEST_URI     $request_uri;
        fastcgi_param DOCUMENT_ROOT   $document_root;
        fastcgi_param PATH_INFO       $fastcgi_path_info;

        # FastCGI logging
        fastcgi_intercept_errors on;
        fastcgi_buffers 16 16k;
        fastcgi_buffer_size 32k;
    }
}
```

**Note**: Adjust the `/home/frappe/frappe-bench/...` path to match your setup.

## Printer Configuration

In your CloudPRNT-capable printer's settings, set the URL to:

```
https://your-domain.tld/cloudprnt/cloudprnt.php
```

Replace `your-domain.tld` with your actual domain name.

## License

MIT