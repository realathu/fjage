package org.arl.fjage.test

import org.arl.fjage.*
import org.arl.fjage.remote.*
import org.arl.fjage.shell.*
import org.arl.fjage.connectors.*
import org.junit.Test

import static org.junit.Assert.assertEquals

class fjagecTest {

  @Test
  void fjageCTest() {
    if (System.getProperty("os.name").startsWith("Windows")) return // skip on Windows
    def platform = new RealTimePlatform()
    def container = new MasterContainer(platform, 5081)
    WebServer.getInstance(8080).add("/", "/org/arl/fjage/web")
    Connector conn = new WebSocketConnector(8080, "/shell/ws")
    def shell = new ShellAgent(new ConsoleShell(conn), new GroovyScriptEngine())
    container.addConnector(new WebSocketConnector(8080, "/ws", true))
    container.add 'shell', shell
    platform.start()
    Thread.sleep(5)
    def ret = 0
    println "Running automated tests."
    def proc = "make -C gateways/c clean test runtest".execute()
    def sout = new StringBuilder(), serr = new StringBuilder()
    proc.consumeProcessOutput(sout, serr)
    proc.waitFor()
    ret = proc.exitValue()
    println "C : out = $sout \n err = $serr \n ret = $ret"
    container.shutdown()
    platform.shutdown()
    assertEquals(ret,0)
  }

}
