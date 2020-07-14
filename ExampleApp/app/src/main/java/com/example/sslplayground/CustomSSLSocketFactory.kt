package com.example.sslplayground

import android.util.Log
import android.widget.ToggleButton
import java.lang.Exception
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketAddress
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory


class CustomSSLSocketFactory(val rsaSwitch : ToggleButton) : SSLSocketFactory() {
    private val defaultFactory = SSLSocketFactory.getDefault() as SSLSocketFactory


    override fun getDefaultCipherSuites(): Array<String> {
        throw RuntimeException("Not Implemented!")
    }

    override fun createSocket(s: Socket?, host: String?, port: Int, autoClose: Boolean): Socket {
        s?.run { close() }
        val socket = defaultFactory.createSocket() as SSLSocket
        if(rsaSwitch.isChecked){
            //Have to downgrade TLS to 1.2, as 1.3 disallows RSA
            socket.enabledProtocols = socket.enabledProtocols.filterNot { it.contains("1.3") }.toTypedArray()
            socket.enabledCipherSuites = socket.supportedCipherSuites.filter { it.startsWith("TLS_RSA") }.toTypedArray()
            Log.i(this.javaClass.name, "Protocols: " + socket.enabledProtocols.joinToString())
            Log.i(this.javaClass.name, "Cipher Suites: " + socket.enabledCipherSuites.joinToString())
        }else{
            socket.enabledCipherSuites = socket.supportedCipherSuites.filterNot { it.startsWith("TLS_RSA") }.toTypedArray()
            Log.i(this.javaClass.name, "Protocols: " + socket.enabledProtocols.joinToString())
            Log.i(this.javaClass.name, "Cipher Suites: " + socket.enabledCipherSuites.joinToString())
        }
        socket.keepAlive = false
        socket.connect(InetSocketAddress(host, port))
        return socket
    }

    override fun createSocket(host: String?, port: Int): Socket {
        throw RuntimeException("Not Implemented!")
    }

    override fun createSocket(p0: String?, p1: Int, p2: InetAddress?, p3: Int): Socket {
        throw RuntimeException("Not Implemented!")
    }

    override fun createSocket(p0: InetAddress?, p1: Int): Socket {
        throw RuntimeException("Not Implemented!")
    }

    override fun createSocket(p0: InetAddress?, p1: Int, p2: InetAddress?, p3: Int): Socket {
        throw RuntimeException("Not Implemented!")
    }

    override fun getSupportedCipherSuites(): Array<String> {
        throw RuntimeException("Not Implemented!")
    }
}