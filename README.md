# Raspi Card Ingest

Interface grafica simples para Raspberry Pi OS que detecta cartoes SD por UUID,
identifica a origem das fotos/videos e copia somente arquivos novos para uma
unidade de destino montada.

## Ideia de organizacao

Destino sugerido:

```text
/media/pi/BackupFotos/
  2026-06-06/
    mini3/
      DCIM/...
      MISC/...
    avata2/
      DCIM/...
    insta360/
      DCIM/...
```

Essa estrutura preserva as pastas originais do cartao, mas separa por data e
origem. Isso facilita achar "o que veio de qual camera" sem perder a estrutura
que alguns softwares de camera esperam.

## Arquivos

- `card_ingest_gui.py`: app grafico para rodar na tela TFT.
- `config.example.json`: exemplo de configuracao.
- `install.sh`: instala dependencias basicas e cria um servico de usuario.
- `raspi-card-ingest.service`: modelo do servico systemd de usuario.

## Estado validado no Raspberry

Configuracao validada em um Raspberry Pi 4 Model B com TFT `tft35a`
(`fb_ili9486`) e SSD SanDisk Extreme via USB.

Cartoes cadastrados:

```text
mini3    UUID  7672-73A2
avata2   UUID  7F32-D83A
insta360 LABEL INSTA360
osmo4    UUID  4FFD-8AEB
```

SSD de destino:

```text
UUID C652-BB53
/mnt/ssd/BackupFotos
```

Importante para esta TFT: havia `fbcp` rodando. O Xorg deve desenhar em
`/dev/fb0`; o `fbcp` copia para a TFT. Se o Xorg desenhar em `/dev/fb1`, a tela
pode ficar preta ou piscar.

Arquivo Xorg usado:

```text
/etc/X11/xorg.conf.d/99-tft-fbdev.conf
```

Conteudo:

```text
Section "Device"
    Identifier "TFT35A"
    Driver "fbdev"
    Option "fbdev" "/dev/fb0"
EndSection

Section "Monitor"
    Identifier "TFT35A-Monitor"
EndSection

Section "Screen"
    Identifier "TFT35A-Screen"
    Device "TFT35A"
    Monitor "TFT35A-Monitor"
EndSection
```

O touch ADS7846 precisou desta matriz:

```bash
xinput set-prop 'ADS7846 Touchscreen' 'Coordinate Transformation Matrix' \
  0 -1 1 \
  -1 0 1 \
  0 0 1
```

O launcher `start-card-ingest.sh` no Raspberry aplica essa matriz, desliga
blanking/DPMS, remove o painel `lxpanel` para liberar a tela inteira e inicia o
app.

## Instalar no Raspberry Pi

Copie esta pasta para o Raspberry, por exemplo:

```bash
mkdir -p ~/apps
cp -r raspi-card-ingest ~/apps/
cd ~/apps/raspi-card-ingest
```

Instale:

```bash
chmod +x install.sh
./install.sh
```

Edite a configuracao:

```bash
nano ~/.config/raspi-card-ingest/config.json
```

Ative o app ao iniciar a sessao:

```bash
systemctl --user daemon-reload
systemctl --user enable --now raspi-card-ingest.service
```

Se o Raspberry ainda nao inicia em modo grafico, ative em:

```bash
sudo raspi-config
```

Depois escolha boot para desktop/autologin se quiser a TFT como painel dedicado.

## Descobrir os UUIDs dos cartoes

Conecte um cartao e rode:

```bash
lsblk -o NAME,PATH,FSTYPE,UUID,LABEL,MOUNTPOINTS
```

Copie o UUID da particao do cartao para `cards` no `config.json`.

Exemplo:

```json
{
  "uuid": "1234-ABCD",
  "name": "mini3"
}
```

Se um cartao nao tiver UUID, defina um rotulo persistente e cadastre por
`label`:

```bash
sudo exfatlabel /dev/sdX1 INSTA360
```

```json
{
  "uuid": "",
  "label": "INSTA360",
  "name": "insta360"
}
```

## Destino

Configure `destination_root` para a pasta da unidade montada, por exemplo:

```json
"destination_root": "/media/pi/BackupFotos"
```

O app nao tenta formatar ou montar a unidade de destino. Ele espera que ela ja
esteja montada, porque isso evita copia acidental para o microSD interno do Pi
quando o HD/SSD nao esta conectado.

Exemplo de entrada do SSD em `/etc/fstab`:

```text
UUID=C652-BB53  /mnt/ssd  exfat  defaults,nofail,uid=1000,gid=1000,umask=0022  0  0
```

## Montagem dos cartoes sem prompt grafico

Para evitar prompts como "Removable medium is inserted" e erros de polkit, os
cartoes conhecidos podem ser cadastrados no `/etc/fstab` com `noauto,user`:

```text
UUID=7672-73A2  /media/mobiker/mini3     exfat  noauto,user,nofail,uid=1000,gid=1000,umask=022  0  0
UUID=7F32-D83A  /media/mobiker/avata2    exfat  noauto,user,nofail,uid=1000,gid=1000,umask=022  0  0
LABEL=INSTA360  /media/mobiker/insta360  exfat  noauto,user,nofail,uid=1000,gid=1000,umask=022  0  0
UUID=4FFD-8AEB  /media/mobiker/osmo4     exfat  noauto,user,nofail,uid=1000,gid=1000,umask=022  0  0
```

O app reconhece tanto particoes (`/dev/sdb1`) quanto cartoes formatados como
disco inteiro (`/dev/sdb`), como aconteceu com o `mini3`.

## Como a copia funciona

O app usa `rsync`:

```bash
rsync -a --ignore-existing --info=progress2 origem/ destino/
```

Ou seja:

- arquivos ja existentes no mesmo caminho relativo sao ignorados;
- arquivos novos sao copiados;
- pastas originais do cartao sao preservadas;
- progresso, velocidade e tempo estimado aparecem na interface.

Cada copia tambem registra um evento em:

```text
~/.local/share/raspi-card-ingest/history.jsonl
```

## Sugestoes opcionais

- Se voce costuma descarregar varios cartoes no mesmo dia, manter `YYYY-MM-DD/card`
  e bom o suficiente.
- Se faz muitos trabalhos por dia, use `folder_template` como
  `{date}/{card}/{time}` para separar cada insercao.
- Para cameras 360 e drones, preservar `DCIM`, `MISC`, `PRIVATE` e pastas
  semelhantes costuma ser melhor do que achatar tudo em uma pasta unica.
