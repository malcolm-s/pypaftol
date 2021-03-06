import sys
import re
import copy
import os
import os.path
import logging
import unicodedata
import datetime
import time             ### Paul B. added to sleep after commiting

import mysql.connector

import paftol
import paftol.tools
import paftol.database.analysis
import paftol.database.production


logger = logging.getLogger(__name__)


def strOrNone(x):
    if x is None:
        return None
    # FIXME: coercing datetime to strings, ORM generator should really handle these separately
    elif isinstance(x, datetime.datetime):
        return str(x)
    else:
        return unicodedata.normalize('NFKD', x).encode('ascii', 'ignore').strip()

    
def intOrNone(x):
    if x is None:
        return None
    else:
        return int(x)
    

def floatOrNone(x):
    if x is None:
        return None
    else:
        return float(x)
    

def findFastaFile(analysisDatabase, fastaFname):
    ''' Finds the targets file from the ReferenceTarget table.

    
    Returns a ReferenceTarget row object. 
    '''
    # Paul B. - now getting file from the ReferenceTarget table
    #for fastaFile in analysisDatabase.fastaFileDict.values():   # fastaFile is a row object containing column headers as values
    for fastaFile in analysisDatabase.referenceTargetDict.values(): 
        # Paul B. filename variable has changed:
        #if fastaFile.filename == fastaFname:
        if fastaFile.targetsFastaFile == fastaFname:
            return fastaFile
    return None


def findFastqFile(analysisDatabase, fastqFname):
    # Paul B. - now using inputSequenceDict:
    # NB - each value of the dict is a row object containing column headers as values
    # which gets returned, either from FastqFile (old db) or InputSequence (new db).
    #for fastqFile in analysisDatabase.fastqFileDict.values():
    for inputSequence in analysisDatabase.inputSequenceDict.values():
        if inputSequence.filename == fastqFname:
            return inputSequence
    return None


class PaftolDatabaseDetails(object):

    reDbusername = re.compile('user: *([^ ]+)')     # Paul B - Renamed to 'user' to fit with the actual keyword required
    reDbpassword = re.compile('password: *([^ ]+)') 
    reDbhost = re.compile('host: *([^ ]+)')
    reDbname = re.compile('database: *([^ ]+)')     # Paul B - Renamed to 'database' to fit with the actual keyword required

    def __init__(self, detailsFile=None):
        if detailsFile is None:
            self.dbusername = None
            self.dbpassword = None
            self.dbhost = None
            self.dbname = None
        else:
            self.readFile(detailsFile)

    def readDetailsLine(self, detailsFile, detailsRe, errorMsg):
        line = detailsFile.readline()
        # sys.stderr.write('got line: "%s"\n' % repr(line))
        m = detailsRe.match(line.strip())
        if m is None:
            raise StandardError, errorMsg
        return m.group(1)

    def readFile(self, detailsFile):
        self.dbusername = self.readDetailsLine(detailsFile, self.reDbusername, 'malformed dbusername line')
        self.dbpassword = self.readDetailsLine(detailsFile, self.reDbpassword, 'malformed dbpassword line')
        self.dbhost = self.readDetailsLine(detailsFile, self.reDbhost, 'malformed dbhost line')
        self.dbname = self.readDetailsLine(detailsFile, self.reDbname, 'malformed dbname line')

    def makeConnection(self):
        return mysql.connector.connection.MySQLConnection(user=self.dbusername, password=self.dbpassword, host=self.dbhost, database=self.dbname)


def getDatabaseDetails(detailsFname):
    databaseDetails = None
    with open(detailsFname, 'r') as f:
        databaseDetails = PaftolDatabaseDetails(f)
    return databaseDetails


def getProductionDatabaseDetails(detailsFname=None):
    if detailsFname is None:
        detailsFname = os.path.join(os.environ['HOME'], '.paftol', 'productiondb.cfg')
    return getDatabaseDetails(detailsFname)


def getAnalysisDatabaseDetails(detailsFname=None):
    if detailsFname is None:
        detailsFname = os.path.join(os.environ['HOME'], '.paftol', 'analysisdb.cfg')
    return getDatabaseDetails(detailsFname)


def getProductionDatabase(detailsFname=None):
    productionDatabaseDetails = getProductionDatabaseDetails(detailsFname)
    connection = productionDatabaseDetails.makeConnection()
    productionDatabase = paftol.database.production.ProductionDatabase(connection)
    return productionDatabase


def getAnalysisDatabase(detailsFname=None):
    analysisDatabaseDetails = getAnalysisDatabaseDetails(detailsFname)
    connection = analysisDatabaseDetails.makeConnection()
    analysisDatabase = paftol.database.analysis.AnalysisDatabase(connection)
    return analysisDatabase


def matchesExpectedFastqFname(fastqFname, sequence):
    if sequence.r1FastqFile is None or sequence.r2FastqFile is None:
        return False
    fastqBasename = os.path.basename(fastqFname)
    return fastqBasename == sequence.r1FastqFile or fastqBasename == sequence.r2FastqFile


def matchesExpectedSequencingRun(sequence, sequencingPoolNumber):
    return sequence.sequencingRun is not None and sequence.sequencingRun.upper() == 'SP%04d' % sequencingPoolNumber

    
def findMatchingSequenceList(productionDatabase, fastqFname, sequencingPoolNumber):
    sequenceList = []
    for sequence in productionDatabase.sequenceDict.values():
        if matchesExpectedFastqFname(fastqFname, sequence):
            if matchesExpectedSequencingRun(sequence, sequencingPoolNumber):
                sequenceList.append(sequence)
    return sequenceList


class ExistingFastqFile(object):

    # FIXME: regular expressions used to search along entire path, spurious matches not impossible
    spNumberRe = re.compile('SP([0-9][0-9][0-9][0-9])((-|_+)[A-Z][A-Za-z0-9_ ()-[\\]]+)?$')
    #paftolPrefixedFastqFnameRe = re.compile('PAFTOL[-_]([0-9]+)_R[12]_[0-9]+\\.fastq')    # Paul B. changed to also match e.g. PAFTOL_007767_1.fastq.gz
    paftolPrefixedFastqFnameRe = re.compile('PAFTOL[-_]([0-9]+)_R?[12](_[0-9]+)?\\.fastq')  

    def __init__(self, rawFastqFname):
        self.rawFastqFname = rawFastqFname

    def findSequencingPoolNumber(self):
        dirname, basename = os.path.split(self.rawFastqFname)
        if dirname != '':
            d, spDirname = os.path.split(dirname)
            sys.stderr.write('spDirname: %s\n' % spDirname)
            m = self.spNumberRe.match(spDirname)
            if m is not None:
                return int(m.group(1))
        return None
    
    def findPaftolPrefixedNumber(self):
        m = self.paftolPrefixedFastqFnameRe.search(self.rawFastqFname)
        if m is not None:
            return int(m.group(1))
        return None


def findSequenceForFastqFname(productionDatabase, fastqFname):
    ''' Paul B. added:
        Finds sequence for fastq filenme using two strategies:
        1. Tries to extract what should be the idSequencing id just after the PAFTOL prefix
           and use that id to look up the correct fastq file.
           NB - some of these ids are idPaftol ids - these will fail
           Many post pilot fastq files have this format, mostly later sequencing runs. 
        2. If paftolPrefixedNumber is None, the real fastq filename and its path will be
           used to obtain the sequencing pool. This pool id will be used to create a list of
           all fastq filenames from that pool. If one is found, that's OK and a symlink will be made.'
    '''

    logList = []
    existingFastqFile = ExistingFastqFile(fastqFname)
    paftolPrefixedNumber = existingFastqFile.findPaftolPrefixedNumber()
    sequencingPoolNumber = existingFastqFile.findSequencingPoolNumber()
    logList.append('fastqFname: %s' % fastqFname)
    logList.append('paftolPrefixedNumber: %s' % paftolPrefixedNumber)
    logList.append('sequencingPoolNumber: %s' % sequencingPoolNumber)
    # logger.debug('raw: %s, paftolPrefixedNumber: %s, spNumber: %s', existingFastqFile.rawFastqFname, paftolPrefixedNumber, sequencingPoolNumber)
    paftolPrefixedNumber = existingFastqFile.findPaftolPrefixedNumber()
    if paftolPrefixedNumber is not None:
        idSequencing = paftolPrefixedNumber
        if idSequencing in productionDatabase.sequenceDict:
            sequence = productionDatabase.sequenceDict[idSequencing]
            if matchesExpectedFastqFname(fastqFname, sequence):
                if sequencingPoolNumber is not None:
                    if matchesExpectedSequencingRun(sequence, sequencingPoolNumber):
                        return sequence
                    else:
                        logList.append('found sequencingRun %s, not consistent with sequencingPoolNumber %d' % (sequence.sequencingRun, sequencingPoolNumber))
            else:
                logList.append('fastqFname %s does not match expected names %s or %s' % (fastqFname, sequence.r1FastqFile, sequence.r2FastqFile))
        else:
            logList.append('no sequence with idSequencing %d' % idSequencing)
    else:
        if sequencingPoolNumber is not None:
            sequenceList = findMatchingSequenceList(productionDatabase, fastqFname, sequencingPoolNumber)
            if len(sequenceList) == 0:
                logList.append('no match by fname found')
            elif len(sequenceList) == 1:
                sequence = sequenceList[0]
                return sequence
            else:
                logList.append('multiple matches: %s' % ', '.join(['%d' % s.idSequencing for s in sequenceList]))
        else:
            logList.append('no sequencingPoolNumber')
    logList.append('unresolved')
    logger.debug(', '.join(logList))
    return None


def findSequenceForFastqFnameOld(productionDatabase, fastqFname):
    paftolPrefixedFastqFnameRe = re.compile('PAFTOL-([0-9]+)_R[12]_[0-9]+\\.fastq')
    spNumberRe = re.compile('(SP[0-9][0-9][0-9][0-9])([^/]*)/([0-9]+).*\\.fastq')
    m = paftolPrefixedFastqFnameRe.match(fastqFname)
    if m is not None:
        idSequencing = int(m.group(1))
        if idSequencing in productionDatabase.sequenceDict:
            sequence = productionDatabase.sequenceDict[idSequencing]
            if matchesExpectedFastqFname(fastqFname, sequence):
                return sequence
        return None
    m = spNumberRe.search(fastqFname)
    if m is not None:
        sequencingRun = m.group(1)
        i = int(m.group(3))
        if i in productionDatabase.sequenceDict:
            sequence = productionDatabase.sequenceDict[i]
            if sequence.sequencingRun == sequencingRun and matchesExpectedFastqFname(fastqFname, sequence):
                return sequence
        for sequence in productionDatabase.sequenceDict.values():
            if sequence.library is not None and sequence.library.sample is not None and sequence.library.sample.specimen is not None and sequence.library.sample.specimen.idPaftol is not None and sequence.library.sample.specimen.idPaftol == i and matchesExpectedFastqFname(fastqFname, sequence):
                return sequence
        return None
    # raise StandardError, 'malformed fastqFname: %s' % fastqFname
    return None


def canonicalSymlinkName(sequence, orientation, gzipped):
    # sys.stderr.write('sequence id=%d\n' % sequence.idSequencing)
    gzExt = ''
    if gzipped:
        gzExt = '.gz'
    return 'PAFTOL_%06d_R%1d.fastq%s' % (sequence.idSequencing, orientation, gzExt)


def parseCanonicalSymlink(symlinkName):
    print 'symlinkName: ', symlinkName
    symlinkRe = re.compile('PAFTOL_([0-9]+)_R([12]).fastq')
    m = symlinkRe.match(symlinkName)
    if m is not None:
        return int(m.group(1)), int(m.group(2))
    return None, None


def makeSymlink(symlinkDirname, sequence, fastqFname):
    """Set up a canonical symlink for a sequence in the specified directory.
    
If a symlink or other file with the computed canonical name already exists, no
symlink is created and a warning is issued.

@param symlinkDirname: directory in which to create the symlink
@type symlinkDirname: C{str}
@param sequence: the sequence for which to generate the symlink
@type sequence: C{paftol.database.production.Sequence} instance
@param fastqFname: name of the fastq file
@type fastqFname: C{str}
    """
    orientation = paftol.tools.fastqOrientation(fastqFname)
    gzipped = paftol.tools.isGzipped(fastqFname)
    symlinkName = canonicalSymlinkName(sequence, orientation, gzipped)
    symlinkPath = os.path.join(symlinkDirname, symlinkName)
    if os.path.lexists(symlinkPath) or os.path.exists(symlinkPath):
        logger.warning('sequence %d: link %s already exists', sequence.idSequencing, symlinkPath)
    else:
        os.symlink(fastqFname, symlinkPath)


def generateUnusedPrimaryKey(cursor, tableName, primaryKeyColumnName='id'):
    sqlStatement = 'SELECT max(%s) FROM %s' % (primaryKeyColumnName, tableName)
    cursor.execute(sqlStatement)
    row = cursor.fetchone()
    maxPk = 0
    if row is not None and row[0] is not None:
        maxPk = int(row[0])
    return maxPk + 1


def insertGene(connection, geneName, geneTypeId):
    ''' Paul B. - 25.5.2020
        Doesn't look like this method is used any more - insert to db done via the analysis.py API
    '''
    lockCursor = connection.cursor()
    lockCursor.execute('LOCK TABLE PaftolGene WRITE')
    try:
        cursor = connection.cursor(prepared=True)
        try:
            paftolGeneId = generateUnusedPrimaryKey(cursor, 'PaftolGene')
            cursor.execute('INSERT INTO PaftolGene (id, geneName, geneTypeId) VALUES (%s, %s, %s)', (paftolGeneId, geneName, geneTypeId, ))
        finally:
            cursor.close()
    finally:
        lockCursor.execute('UNLOCK TABLES')
        lockCursor.close()
    return paftolGeneId


def insertFastaFile(connection, fastaFname, dirname=None):
    ''' Paul B. added:
        The FastaFile table doesn't exist anymore.
    '''

    fastaPath = fastaFname
    if dirname is not None:
        fastaPath = os.path.join(dirname, fastaFname)
    md5 = paftol.tools.md5HexdigestFromFile(fastaPath)
    numSequences = len(paftol.tools.fastaSeqRecordList(fastaPath))
    lockCursor = connection.cursor()
    lockCursor.execute('LOCK TABLE FastaFile WRITE')
    try:
        cursor = connection.cursor(prepared=True)
        try:
            fastaFileId = generateUnusedPrimaryKey(cursor, 'FastaFile')
            cursor.execute('INSERT INTO FastaFile (id, filename, md5sum, numSequences) VALUES (%s, %s, %s, %s)', (fastaFileId, fastaFname, md5, numSequences, ))
        finally:
            cursor.close()
    finally:
        lockCursor.execute('UNLOCK TABLES')
        lockCursor.close()
    return fastaFileId


def insertFastaExternalAccession(connection, fastaFilename, dataOriginAcronym, accession):
    lockCursor = connection.cursor()
    lockCursor.execute('LOCK TABLE ExternalAccesion WRITE')
    try:
        cursor = connection.cursor(prepared=True)
        try:
            fastaFileId = generateUnusedPrimaryKey(cursor, 'FastaFile')
            cursor.execute('INSERT INTO FastaFile (id, filename, md5sum, numSequences) VALUES (%s, %s, %s, %s)', (fastaFileId, fastaFname, md5, numSequences, ))
        finally:
            cursor.close()
    finally:
        lockCursor.execute('UNLOCK TABLES')
        lockCursor.close()
    return fastaFileId


def addFastqStats(connection, fastqcStats):
    logger.debug('starting')
    fastqcSummaryStats = paftol.tools.FastqcSummaryStats(fastqcStats)
    cursor = connection.cursor(prepared=True)
    try:
        fastqStatsId = generateUnusedPrimaryKey(cursor, 'FastqStats')
        logger.debug('meanAdapterContent: %s, maxAdapterContent: %s', fastqcSummaryStats.meanAdapterContent, fastqcSummaryStats.maxAdapterContent)        
        cursor.execute('INSERT INTO FastqStats (id, numReads, qual28, meanA, meanC, meanG, meanT, stddevA, stddevC, stddevG, stddevT, meanN, stddevN, meanAdapterContent, maxAdapterContent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', (fastqStatsId, fastqcSummaryStats.numReads, fastqcSummaryStats.qual28, fastqcSummaryStats.meanA, fastqcSummaryStats.meanC, fastqcSummaryStats.meanG, fastqcSummaryStats.meanT, fastqcSummaryStats.stddevA, fastqcSummaryStats.stddevC, fastqcSummaryStats.stddevG, fastqcSummaryStats.stddevT, fastqcSummaryStats.meanN, fastqcSummaryStats.stddevN, fastqcSummaryStats.meanAdapterContent, fastqcSummaryStats.maxAdapterContent))
    finally:
        cursor.close()
    return fastqStatsId


# Paul B. - made a copy of this function just below to use for the new table structure.
def preInsertCheckPaftolFastqFile(paftolFastqFile):
    if paftolFastqFile.id is not None:
        raise StandardError, 'illegal state: PaftolFastqFile instance has id %d, not None' % paftolFastqFile.id
    if paftolFastqFile.fastqFile is None:
        raise StandardError, 'illegal state: PaftolFastqFile has fastqFile attribute set to None'
    if paftolFastqFile.fastqFile.fastqStats is not None and  paftolFastqFile.fastqFile.fastqStats.id is not None:
        raise StandardError, 'illegal state: new FastqFile %s has existing FastqStats %d' % (paftolFastqFile.fastqFile.filename, paftolFastqFile.fastqFile.fastqStats.id)

# Paul B - new method for new db schema - not actually sure why we need to do these checks.
#          Why no other checks on other variables and objects?
#          Actually does catch this situation: e.g. 5853_R1.fastq if --dataOrigin == PAFTOL
def preInsertCheckInputSequence(inputSequence):
    if inputSequence.id is not None:
        raise StandardError, 'illegal state: InputSequence instance has id %d, not None' % inputSequence.id
    # Paul B. - added and conditionals - one of these foreign keys needs to be defined: 
    #if inputSequence.paftolSequence is None
    if inputSequence.paftolSequence is None and inputSequence.OneKP_Sequence is None and inputSequence.sraRunSequence is None and inputSequence.annotatedGenome is None:
        #raise StandardError, 'illegal state: inputSequence has paftolSequence attribute set to None'
        raise StandardError, 'illegal state: inputSequence has to have a value for one of these attributes: paftolSequence, OneKP_Sequence, sraRunSequence and annotatedGenome'
    if inputSequence.fastqStats is not None and  inputSequence.fastqStats.id is not None:
        raise StandardError, 'illegal state: new inputSequence %s has existing FastqStats %d' % (inputSequence.filename, inputSequence.fastqStats.id)


### Paul B. added this method - redundant with addPaftolFastqFiles() above - can delete
def insertPaftolFastqFileList(connection, paftolFastqFileList):
    for paftolFastqFile in paftolFastqFileList:
        preInsertCheckPaftolFastqFile(paftolFastqFile)
    insertedPaftolFastqFileList = []        ### Paul B. - this never seems to be used
    transactionSuccessful = False
    # Paul B. - removed table locking and introduced auto_increment for each primary key:
    #lockCursor = connection.cursor()
    #lockCursor.execute('LOCK TABLE PaftolFastqFile WRITE, FastqFile WRITE, FastqStats WRITE')
    try:
        cursor = connection.cursor(prepared=True)
        try:
            for paftolFastqFile in paftolFastqFileList:
                insertedPaftolFastqFile = copy.deepcopy(paftolFastqFile)
                # Paul B - altered for auto_increment:
                #insertedPaftolFastqFile.id = generateUnusedPrimaryKey(cursor, 'PaftolFastqFile')
                #insertedPaftolFastqFile.fastqFile.id = generateUnusedPrimaryKey(cursor, 'FastqFile')
                if insertedPaftolFastqFile.fastqFile.fastqStats is not None:
                    # Paul B. removed - now using auto_increment
                    #insertedPaftolFastqFile.fastqFile.fastqStats.id = generateUnusedPrimaryKey(cursor, 'FastqStats')
                    insertedPaftolFastqFile.fastqFile.fastqStats.insertIntoDatabase(cursor)
                    insertedPaftolFastqFile.fastqFile.fastqStats.id = cursor.lastrowid
                insertedPaftolFastqFile.fastqFile.insertIntoDatabase(cursor)
                insertedPaftolFastqFile.fastqFile.id = cursor.lastrowid
                insertedPaftolFastqFile.insertIntoDatabase(cursor)
                insertedPaftolFastqFile.id = cursor.lastrowid
            connection.commit()
            transactionSuccessful = True
        finally:
            if not transactionSuccessful:
                connection.rollback()
                print "ERROR: commit unsucessful for insertedPaftolFastqFile.fastqFile.fastqStats.id: ", insertedPaftolFastqFile.fastqFile.fastqStats.id
                print "ERROR: commit unsucessful for insertedPaftolFastqFile.fastqFile.id: ", insertedPaftolFastqFile.fastqFile.id
                print "ERROR: commit unsucessful for insertedPaftolFastqFile.id: ", insertedPaftolFastqFile.id
            cursor.close()
    finally:
        if not transactionSuccessful:
            connection.rollback()
        #lockCursor.execute('UNLOCK TABLES')
        #lockCursor.close()
    return transactionSuccessful


# Paul B. - added a new method equivalent to and to replace insertPaftolFastqFileList():
def insertInputSequenceList(connection, inputSequenceList, newPaftolSequence):
    for inputSequence in inputSequenceList:
        # Paul B.: preInsertCheckPaftolFastqFile(paftolFastqFile)
        # Now checking entries from the InputSequence table:
        preInsertCheckInputSequence(inputSequence)
    transactionSuccessful = False
    # Paul B. - removed table locking and introduced auto_increment for each primary key:
    #lockCursor = connection.cursor()
    #lockCursor.execute('LOCK TABLE PaftolFastqFile WRITE, FastqFile WRITE, FastqStats WRITE')
    try:
        cursor = connection.cursor(prepared=True)
        # Inserting into paftolSequence once, then id is available for both fastq files.
        # An alternative would be to bring in the newPaftolSequence object to avoid 
        # accessing the PaftolSequence table via an inputSequence object - now doing this.
        #inputSequenceList[0].paftolSequence.insertIntoDatabase(cursor)
        #inputSequenceList[0].paftolSequence.id = cursor.lastrowid
        newPaftolSequence.insertIntoDatabase(cursor)
        newPaftolSequence.id = cursor.lastrowid
        if newPaftolSequence.id is not None:
            print "newPaftolSequence.id:", newPaftolSequence.id
        #print "inputSequenceList[0].paftolSequence.id: ", inputSequenceList[0].paftolSequence.id
        #print "inputSequenceList[1].paftolSequence.id: ", inputSequenceList[1].paftolSequence.id  # proves paftolSequence object is the same instance.
        try:
            for inputSequence in inputSequenceList:
                # Paul B. - don't understand the purpose of the deepcopy - deepcopy is a copy whose items are different instances I think
                #           It now causes an error so will removed it: RuntimeError: maximum recursion depth exceeded while calling a Python object
                #insertedInputSequence = copy.deepcopy(inputSequence)
                #print "insertedInputSequence.fastqStats.numReads: ", insertedInputSequence.fastqStats.numReads
                #print "insertedInputSequence.paftolSequence.idSequencing: ", insertedInputSequence.paftolSequence.idSequencing
                # Paul B - altered for auto_increment:
                #insertedPaftolFastqFile.id = generateUnusedPrimaryKey(cursor, 'PaftolFastqFile')
                #insertedPaftolFastqFile.fastqFile.id = generateUnusedPrimaryKey(cursor, 'FastqFile')
                ###if insertedInputSequence.fastqStats is not None:
                if inputSequence.fastqStats is not None:
                    # Paul B. removed - now using auto_increment
                    #insertedPaftolFastqFile.fastqFile.fastqStats.id = generateUnusedPrimaryKey(cursor, 'FastqStats')
                    ###insertedInputSequence.fastqStats.insertIntoDatabase(cursor)
                    ###insertedInputSequence.fastqStats.id = cursor.lastrowid
                    ###print 'insertedInputSequence.fastqStats.id: '           #, insertedInputSequence.fastqStats.id
                    inputSequence.fastqStats.insertIntoDatabase(cursor)
                    inputSequence.fastqStats.id = cursor.lastrowid
                    if inputSequence.fastqStats.id is not None:
                        print 'inputSequence.fastqStats.id: ', inputSequence.fastqStats.id      # inputSequence.fastqStats.id
                # InputSequence requires the primary keys from FastqStats and PaftolSequence tables (retrieved above) 
                ###insertedInputSequence.insertIntoDatabase(cursor)
                ###insertedInputSequence.id = cursor.lastrowid
                inputSequence.insertIntoDatabase(cursor)
                inputSequence.id = cursor.lastrowid
                if inputSequence.id is not None:
                    print 'insertedInputSequence.id: ', inputSequence.id                    # insertedInputSequence.id
                    print 'insertedInputSequence.filename: ', inputSequence.filename        # insertedInputSequence.filename
                    ### if inputSequence.dataOrigin.dataOriginId == 'OneKP_Transcript' or if inputSequence.dataOrigin.dataOriginId == 'AG':
                    ###     addExternalGenes(XXXX=inputSequenceList[0], XXXX=inputSequence.id) - shoudl only be one file - would be good to break out of loop after
                    ### Try to only use objects 
            connection.commit()
            transactionSuccessful = True
        finally:
            if not transactionSuccessful:
                connection.rollback()
                print "ERROR: commit unsucessful for insertedInputSequence.fastqStats.id: "             #, insertedInputSequence.fastqStats.id
                print "ERROR: commit unsucessful for insertedInputSequence.id: "                        #, insertedInputSequence.id
            cursor.close()
    finally:
        if not transactionSuccessful:
            connection.rollback()
            print "ERROR: commit unsucessful for newPaftolSequence"      #, inputSequenceList[0].paftolSequence.id
        #lockCursor.execute('UNLOCK TABLES')
        #lockCursor.close()
    return transactionSuccessful


def fastqStatsFromFastqcStats(fastqcStats):
    fastqcSummaryStats = paftol.tools.FastqcSummaryStats(fastqcStats)
    # Paul B. - removed None in 1st position for auto_increment:
    return paftol.database.analysis.FastqStats(numReads=fastqcSummaryStats.numReads, qual28=fastqcSummaryStats.qual28, meanA=fastqcSummaryStats.meanA, meanC=fastqcSummaryStats.meanC, meanG=fastqcSummaryStats.meanG, meanT=fastqcSummaryStats.meanT, stddevA=fastqcSummaryStats.stddevA, stddevC=fastqcSummaryStats.stddevC, stddevG=fastqcSummaryStats.stddevG, stddevT=fastqcSummaryStats.stddevT, meanN=fastqcSummaryStats.meanN, stddevN=fastqcSummaryStats.stddevN, meanAdapterContent=fastqcSummaryStats.meanAdapterContent, maxAdapterContent=fastqcSummaryStats.maxAdapterContent)


def addPaftolFastqFiles(fastqFnameList=None, dataOriginAcronym=None, fastqPath=None, sampleId=None):    # Paul B. changed to include path to fastq file(s) and sampleId (for use with non-paftol data)
    ''' Adds input sequence file(s) info into the paftol_da database e.g. fastq, fasta files

    Was specific to PAFTOL only data, now can handle more data set types as defined in the DataOrigin table.
    '''
    # Paul B. added conditional:
    ### NB - 21.7.2020 - code to connect to the production db already existed but then never did anything.
    ###                  But i think i can now use this connec tion to look up the idSeequencing with the 
    ###                  sampleId for other non-paftol data sets, if it exists
    ###                  Waiting for Berta to add the sampleId column. 
    if dataOriginAcronym == 'paftol':
        productionDatabaseDetails = getProductionDatabaseDetails()
        connection = productionDatabaseDetails.makeConnection()
        productionDatabase = paftol.database.production.ProductionDatabase(connection)
        connection.close()
    analysisDatabaseDetails = getAnalysisDatabaseDetails()
    connection = analysisDatabaseDetails.makeConnection()
    analysisDatabase = paftol.database.analysis.AnalysisDatabase(connection)
    # Paul B. added to check dataOriginName with name in DataOrigin table:
    # NB - this check means that the --dataOrigin flag is no longer strictly an 'option'
    # but I followed the logic for --geneType flag in addTargetsFile 
    dataOrigin = findDataOrigin(analysisDatabase, dataOriginAcronym)     # Paul B. - returns a DataOrigin object
    if dataOrigin is None:
        connection.close()
        raise StandardError, 'Data origin entry is incorrect. Allowed values are: \'PAFTOL\', \'OneKP_Transcripts\', \'OneKP_Reads\'  \'SRA\', \'AG\' '
        ### NB - I think argparse can handle this if required= is set - if so can delete above code
    # Paul B. - need to create a new PaftolSequence object with info from read1 file only.
    # First, get the idSequencing from the R1 file ('paftol' data set only)
    newDatasetSequence = None           # Paul B. added - new table object for any data set type
    idSequencing = None                 # Paul B. added - used later in method so needs to be global (?)
    if dataOriginAcronym == 'PAFTOL':   # Paul B. added
        idSequencing, orientation = parseCanonicalSymlink(fastqFnameList[0])
        print 'idSequencing: ', idSequencing , 'orientation: ', orientation
        #newPaftolSequence = paftol.database.analysis.PaftolSequence(idSequencing=idSequencing, replicate=None)
        if idSequencing is not None:    # idSequencing may not be found
            newDatasetSequence = paftol.database.analysis.PaftolSequence(idSequencing=idSequencing, replicate=None)
        else:
            logger.warning('not a canonical PAFTOL fastq name, can\'t obtain idSequencing identifier: %s', fastqFnameList[0])
        # Paul B. - made object generic for all dataset table types
    # Paul B. - For datasets other than PAFTOL, will acquire the sampleId directly from the sampleId input flag.
    ### NB - might be able to handle data set options better by using word matches rather than conditonals 
    elif dataOriginAcronym == 'OneKP_Transcripts' or dataOriginAcronym == 'OneKP_Reads':  # sampleId should exist here
        ### To do: Find the idSequencing identifier from the production db using the sample identifier
        ### Waiting for idSequencing/sampelId's to be created in production db, then add idSequencing=idSequencing below
        newDatasetSequence = paftol.database.analysis.OneKP_Sequence(sampleId=sampleId)
    elif dataOriginAcronym == 'SRA': 
        newDatasetSequence = paftol.database.analysis.SRA_RunSequence(accessionId=sampleId)
    elif dataOriginAcronym == 'AG':
        newDatasetSequence = paftol.database.analysis.AnnotatedGenome(accessionId=sampleId)
    else:
        raise StandardError, 'unknown data origin: %s' % dataOriginAcronym

    # Paul B. - removed: newPaftolFastqFileList = []
    newInputSequenceList = []
    for fastqFname in fastqFnameList:
        print 'fastqFname: ', fastqFname
        # Paul B. - moved above outside of loop:
        #idSequencing, orientation = parseCanonicalSymlink(fastqFname)
        #print 'idSequencing: ', idSequencing , 'orientation: ', orientation
        if idSequencing is not None or sampleId is not None:    # Paul B added sampleId
            md5sum = paftol.tools.md5HexdigestFromFile(fastqFname)
            #fastqFile = findFastqFile(analysisDatabase, fastqFname)
            # Paul - now returns an inputSequence object
            inputSequence = findFastqFile(analysisDatabase, fastqFname)
            # Paul B. - changed: if fastqFile is None:
            # NB - if you insert the same fastq file twice i.e. two R1 files by mistake, this will not
            #      will not be detected here because the first fastq file is not yet uploaded until 
            #      the insert method is called.
            if inputSequence is None:

                 # Paul B. recreated the full path to the raw fastq files (have to be unzipped for the above commands!):
                if fastqPath is not None:
                    # Only add .gz ending for fastq files (assumed to be zipped for the raw files):
                    if re.search('.fastq$|.fq$', fastqFname) is not None: 
                        fastqPathName = fastqPath + '/' + fastqFname + '.gz'
                    else:
                        fastqPathName = fastqPath + '/' + fastqFname
                    print 'fastqPathName: ', fastqPathName
                else:
                    fastqPathName = None
                    print 'fastqPathName: ', fastqPathName

                # Paul B added:
                if dataOriginAcronym == 'PAFTOL' or dataOriginAcronym == 'OneKP_Reads' or dataOriginAcronym == 'SRA':
                    fastqcStats = paftol.tools.generateFastqcStats(fastqFname)
                    newFastqStats = fastqStatsFromFastqcStats(fastqcStats)      # Paul B. NB - this is a database table object
               
                # Paul B - altered to fit with auto_increment + to add the full path to the fastq file:
                #newFastqFile = paftol.database.analysis.FastqFile(filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=newFastqStats)            
                # Paul B. corrected from: newPaftolFastqFile = paftol.database.analysis.PaftolFastqFile(None, idSequencing, newFastqFile)
                # Paul B - also altered for auto_increment:
                #newPaftolFastqFile = paftol.database.analysis.PaftolFastqFile(idSequencing=idSequencing, fastqFile=newFastqFile)
                ###print 'Filename in newPaftolFastqFile: ', newPaftolFastqFile.newFastqFile.filename  - NB - why does this not work?
                #newPaftolFastqFileList.append(newPaftolFastqFile)
                ### NBNB - it may be that  newPaftolFastqFile goes inside newFastqFile rather than the other way round due to which table
                ### has the foreign key - it might be clear from the DDL.
                # Paul B - 18.2.2020
                # Updated to use the new tables of the new db structure:
                #newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, sequenceType=None, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=newFastqStats, paftolSequence=newPaftolSequence, sraRunSequence=None, OneKP_Sequence=None, annotatedGenome=None)
                #newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=newFastqStats, paftolSequence=newPaftolSequence)
                # Paul B - modified above line to be able to input a generic data set table object from above and add newFastqStats but only for data sets with fastq files.
                if dataOriginAcronym == 'PAFTOL':
                    newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=newFastqStats, paftolSequence=newDatasetSequence)
                elif dataOriginAcronym == 'OneKP_Reads':
                    newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=newFastqStats, OneKP_Sequence=newDatasetSequence)
                elif dataOriginAcronym == 'SRA':
                    newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=newFastqStats, sraRunSequence=newDatasetSequence)
                elif dataOriginAcronym == 'OneKP_Transcripts':
                    newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=None, OneKP_Sequence=newDatasetSequence)
                elif dataOriginAcronym == 'AG': 
                    newInputSequence = paftol.database.analysis.InputSequence(dataOrigin=dataOrigin, filename=fastqFname, pathName=fastqPathName, md5sum=md5sum, fastqStats=None, annotatedGenome=newDatasetSequence)

                #print dir(newInputSequence)
                #print "1.Looking at newInputSequence contents: ", newInputSequence.filename
                newInputSequenceList.append(newInputSequence)
            else:
                # Paul B. - if fastqFile.md5sum == md5sum:
                if inputSequence.md5sum == md5sum:
                    logger.info('fastq file %s already in database, verified md5sum', fastqFname)
                    # Paul B. added:
                    print logger.warning('fastq file %s already in database, verified by md5sum', fastqFname)
                else:
                    # Paul B. - raise StandardError, 'fastq file %s in database with md5sum = %s, but found md5sum = %s' % (fastqFname, fastqFile.md5sum, md5sum)
                    raise StandardError, 'fastq file %s in database with md5sum = %s, but found md5sum = %s' % (fastqFname, InputSequence.md5sum, md5sum)
        else:
### Need to change message - for other datatypes
            logger.warning('No sample identifier obtainable for %s', fastqFname)
    # Paul B. - transactionSuccessful = insertPaftolFastqFileList(connection, newPaftolFastqFileList)
    # NB - as the code was, it seems that the insertInputSequenceList() method was entered even though the files were
    #      already in the db. If so, newInputSequenceList would be empty so I thought a conditional was required.
    #      However the method will just fall silent if list is empty.
    #      Can't really test the array in the method though, will get e.g. index out of range error.
    # However, newPaftolSequence (now generically newDatasetSequence) is now included in the method so it will not fall silent if list is empty, so need a conditional now so as not to enter insertInputSequenceList method if list is empty.
    if newInputSequenceList:
        ###print "2.Looking at array contents: ", newInputSequenceList[0].filename    # gives error unless occupied
        ###dir(newInputSequenceList[0])     # gives error unless occupied
        transactionSuccessful = insertInputSequenceList(connection, newInputSequenceList, newDatasetSequence)
        return transactionSuccessful
    else:
        logger.warning('Not attempting to insert filename info into database')


def findGeneType(analysisDatabase, geneTypeName):
    for geneType in analysisDatabase.geneTypeDict.values():
        if geneType.geneTypeName == geneTypeName:
            return geneType
    return None


def findDataOrigin(analysisDatabase, dataOriginAcronym):
    for dataOrigin in analysisDatabase.dataOriginDict.values():
        if dataOrigin.acronym == dataOriginAcronym:
            return dataOrigin
    return None


def addTargetsFile(targetsFname, fastaPath=None, description=None, insertGenes=False, geneTypeName=None):
    if insertGenes and geneTypeName is None:
        raise StandardError, 'illegal state: insertion of new genes requested but no gene type name given'
     # Paul B. - recreated the full path to the fasta target file (assumed to be within the paftol dir):
    if fastaPath is not None:       
        fastaPath = fastaPath + '/' + targetsFname
        print 'fastqPath: ', fastaPath
    else:
        fastaPath = None
        print 'fastaPath: ', fastaPath
    md5sum = paftol.tools.md5HexdigestFromFile(targetsFname)        
    paftolTargetSet = paftol.PaftolTargetSet()         # A PaftolTargetSet object
    paftolTargetSet.readFasta(targetsFname)
    numSequences = len(paftolTargetSet.getSeqRecordList())
    analysisDatabaseDetails = getAnalysisDatabaseDetails()
    connection = analysisDatabaseDetails.makeConnection()
    analysisDatabase = paftol.database.analysis.AnalysisDatabase(connection)
    geneType = findGeneType(analysisDatabase, geneTypeName)     # Paul B. - a geneType object
    if geneType is None:
        connection.close()
        raise StandardError, 'unknown gene type: %s' % geneTypeName
    targetsFile = findFastaFile(analysisDatabase, targetsFname)
    if targetsFile is not None:
        connection.close()
        if targetsFile.md5sum == md5sum:
            logger.info('targets file %s already in database, verified md5sum', targetsFname)
            #print 'ok'
            return
        else:
            raise StandardError, 'targets file %s in database with md5sum = %s, but found md5sum = %s' % (targetsFname, targetsFile.md5sum, md5sum)
    # connection.start_transaction(isolation_level='REPEATABLE READ', readonly=False)
    # Paul B. - changed to fit with the auto_increment
    #targetsFastaFile = paftol.database.analysis.FastaFile(None, targetsFname, md5sum, None, numSequences)
    # Paul B. - now sending to the ReferenceTarget table:
    #targetsFastaFile = paftol.database.analysis.FastaFile(targetsFname, None, md5sum, None, numSequences)
    knownGeneNameList = [paftolGene.geneName for paftolGene in analysisDatabase.paftolGeneDict.values()]
    missingGeneNameList = []
    for geneName in paftolTargetSet.paftolGeneDict.keys():
        if geneName not in knownGeneNameList:
            missingGeneNameList.append(geneName)
    if not insertGenes and len(missingGeneNameList) > 0:
        connection.close()
        raise StandardError, 'missing genes: %s' % ', '.join(missingGeneNameList)
    newPaftolGeneList = []
    for missingGeneName in missingGeneNameList:
        # Paul B. - changed to fit with the auto_increment - 25.5.2020 - also added the exemplarGeneId:
        #newPaftolGene = paftol.database.analysis.PaftolGene(None, missingGeneName, geneType)
        newPaftolGene = paftol.database.analysis.PaftolGene(missingGeneName, geneType, None)
        newPaftolGeneList.append(newPaftolGene)
    paftolGeneDict = {}
    for newPaftolGene in newPaftolGeneList:
        paftolGeneDict[newPaftolGene.geneName] = newPaftolGene
    for paftolGene in analysisDatabase.paftolGeneDict.values():
        paftolGeneDict[paftolGene.geneName] = paftolGene
    referenceTargetList = []
    for paftolGene in paftolTargetSet.paftolGeneDict.values():
        for paftolTarget in paftolGene.paftolTargetDict.values():
            # Paul B. - changed to fit with the auto_increment:
            #referenceTargetList.append(paftol.database.analysis.ReferenceTarget(None, paftolGeneDict[paftolGene.name], paftolTarget.organism.name, len(paftolTarget.seqRecord), targetsFastaFile))
            # Paul B. - now changing to add the targets file info:
            #referenceTargetList.append(paftol.database.analysis.ReferenceTarget(paftolGeneDict[paftolGene.name], paftolTarget.organism.name, len(paftolTarget.seqRecord), targetsFastaFile))
#### 27.2.2020 - ACTUALLY NOW planning TO CHANGE AGAIN AND CREATE A SEPARATE TABLE TO HOUSE THE FASTA FILE
            referenceTargetList.append(paftol.database.analysis.ReferenceTarget(paftolGene=paftolGeneDict[paftolGene.name], paftolOrganism=paftolTarget.organism.name, paftolTargetLength=len(paftolTarget.seqRecord), \
            targetsFastaFile=targetsFname, targetsFastaFilePathName=fastaPath, md5sum=md5sum, numTargetSequences=numSequences))
    transactionSuccessful = False
    # Paul B. - removed table locking and introduced auto_increment for each primary key:
    #lockCursor = connection.cursor()
    #lockCursor.execute('LOCK TABLE FastaFile WRITE, FastqFile WRITE, FastqStats WRITE, GeneType WRITE, PaftolGene WRITE, ReferenceTarget WRITE')
    try:
        logger.info('adding new targets file %s', targetsFname)
        cursor = connection.cursor(prepared=True)
        try:
            for newPaftolGene in newPaftolGeneList:
                #newPaftolGene.id = generateUnusedPrimaryKey(cursor, 'PaftolGene')
                newPaftolGene.insertIntoDatabase(cursor)
                newPaftolGene.id = cursor.lastrowid
                #print "newPaftolGene.id: ", newPaftolGene.id
            #targetsFastaFile.id = generateUnusedPrimaryKey(cursor, 'FastaFile')
            # Paul B. - changed to send to ReferenceTarget table instead:
            #targetsFastaFile.insertIntoDatabase(cursor)
            #targetsFastaFile.id = cursor.lastrowid
            #print "targetsFastaFile.id: ", targetsFastaFile.id
            for referenceTarget in referenceTargetList:
                #referenceTarget.id = generateUnusedPrimaryKey(cursor, 'ReferenceTarget')
                referenceTarget.insertIntoDatabase(cursor)
                referenceTarget.id = cursor.lastrowid
                #print "referenceTarget.id: ", referenceTarget.id
            connection.commit()
            transactionSuccessful = True
        finally:
            if not transactionSuccessful:
                connection.rollback()
                 # Paul B added:
                print "ERROR: commit unsucessful for newPaftolGene.id: ", newPaftolGene.id
                #print "ERROR: commit unsucessful for targetsFastaFile.id: ", targetsFastaFile.id
                print "ERROR: commit unsucessful for referenceTarget.id: ", referenceTarget.id
            cursor.close()
    finally:
        if not transactionSuccessful:
            connection.rollback()
        #lockCursor.execute('UNLOCK TABLES')
        #lockCursor.close()
    connection.close()
    return transactionSuccessful


def findFastqFiles(analysisDatabase, result):
    fwdFastqFname = os.path.basename(result.forwardFastq)
    revFastqFname = os.path.basename(result.reverseFastq)
    fwdFastqFile = None
    revFastqFile = None
    # Paul B. - changed to use inputSequenceDict - each element contains a row object:
    #for fastqFile in analysisDatabase.fastqFileDict.values():
    for fastqFile in analysisDatabase.inputSequenceDict.values():
        if fastqFile.filename == fwdFastqFname:
            fwdFastqFile = fastqFile
        if fastqFile.filename == revFastqFname:
            revFastqFile = fastqFile
    return fwdFastqFile, revFastqFile


def findContigRecoveryForFastqFname(analysisDatabase, fastqFname):
    fastqFile = findFastqFile(analysisDatabase, fastqFname)
    if fastqFile is None:
        return None
    if len(fastqFile.contigRecoveryFwdFastqList) + len(fastqFile.contigRecoveryRevFastqList) > 1:
        raise StandardError, 'multiple ContigRecovery instances for %s: %s' % (fastqFname, ', '.join(['%d' % cr.id for cr in fastqFile.contigRecoveryFwdFastqList +  fastqFile.contigRecoveryRevFastqList]))
    if len(fastqFile.contigRecoveryFwdFastqList) == 1:
        return fastqFile.contigRecoveryFwdFastqList[0]
    if len(fastqFile.contigRecoveryRevFastqList) == 1:
        return fastqFile.contigRecoveryRevFastqList[0]
    return None


def preRecoveryCheck(forwardFastqFname, reverseFastqFname):
    msgList = []
    analysisDatabase = getAnalysisDatabase()
    contigRecovery = findContigRecoveryForFastqFname(analysisDatabase, forwardFastqFname)
    if contigRecovery is not None:
        msgList.append('recovery already done for %s (contigRecovery.id = %d)' % (forwardFastqFname, contigRecovery.id))
    contigRecovery = findContigRecoveryForFastqFname(analysisDatabase, reverseFastqFname)
    if contigRecovery is not None:
        msgList.append('recovery already done for %s (contigRecovery.id = %d)' % (reverseFastqFname, contigRecovery.id))
    if len(msgList) > 0:
        raise StandardError, ', '.join(msgList)


def findContigRecoveryForSequencing(analysisDatabase, idSequencing):
    fastqFileList = []
    for paftolFastqFile in analysisDatabase.paftolFastqFileDict.values():
        if paftolFastqFile.idSequencing is not None and paftolFastqFile.idSequencing == idSequencing:
            if paftolFastqFile.fastqFile is None:
                raise StandardError, 'illegal state: PaftolFastqFile instance %d has no fastqFile' % paftolFastqFile.id
            fastqFile.List.append(paftolFastqFile.fastqFile)
    contigRecoveryList = []
    for fastqFile in fastqFileList:
        for contigRecovery in fastqFile.contigRecoveryFwdFastqList:
            if contigRecovery not in contigRecoveryList:
                contigRecoveryList.append(contigRecovery)
        for contigRecovery in fastqFile.contigRecoveryRevFastqList:
            if contigRecovery not in contigRecoveryList:
                contigRecoveryList.append(contigRecovery)
    if len(contigRecoveryList) == 0:
        return None
    elif len(contigRecoveryList) == 1:
        return contigRecoveryList[0]
    else:
        raise StandardError, 'idSequencing %d: found multiple ContigRecovery instances: %s' % (idSequencing, ', '.join(['%d' % cr.id for cr in contigRecoveryList]))

    
def findReferenceTarget(analysisDatabase, geneName, paftolOrganism):
    logger.debug('searching for %s-%s', paftolOrganism, geneName)
    for referenceTarget in analysisDatabase.referenceTargetDict.values():
        logger.debug('checking %s-%s', referenceTarget.paftolOrganism, referenceTarget.paftolGene.geneName)
        if referenceTarget.paftolOrganism == paftolOrganism and referenceTarget.paftolGene.geneName == geneName:
            return referenceTarget
    return None


def addRecoveryResult(result):
    analysisDatabaseDetails = getAnalysisDatabaseDetails()      ### PaulB - returns a mysql.connector connection object
    connection = analysisDatabaseDetails.makeConnection()
    #connection.autocommit = True                               ### Paul B. - tried autocommit
    analysisDatabase = paftol.database.analysis.AnalysisDatabase(connection)
    # Paul B. - 25.2.2020 - now acesses the ReferenceTarget table instead:
    ### NBNB - this is not good I think but it works - targetsFastaFile needs to have its own table
    targetsFastaFile = findFastaFile(analysisDatabase, result.paftolTargetSet.fastaHandleStr)
    numMappedReads = len(result.paftolTargetSet.getMappedReadNameSet())
    numUnmappedReads = result.paftolTargetSet.numOfftargetReads
    if targetsFastaFile is None:
        # raise StandardError, 'targets file "%s" not in database' % result.paftolTargetSet.fastaHandleStr
        logger.info('unknown targets file "%s" -- continuing')
    # Paul B. - alter method find fastq files in the InputSequence table:
    fwdFastqFile, revFastqFile = findFastqFiles(analysisDatabase, result)
    if fwdFastqFile is None:
        raise StandardError, 'forward fastq file "%s" not in database' % result.forwardFastq
    if revFastqFile is None:
        raise StandardError, 'reverse fastq file "%s" not in database' % result.reverseFastq
    trimmedForwardFastqStats = None
    if result.forwardTrimmedPairedFastqcStats is not None:
        trimmedForwardFastqStats = fastqStatsFromFastqcStats(result.forwardTrimmedPairedFastqcStats)
    trimmedReverseFastqStats = None
    if result.reverseTrimmedPairedFastqcStats is not None:
        trimmedReverseFastqStats = fastqStatsFromFastqcStats(result.reverseTrimmedPairedFastqcStats)
    paftolGeneDict = {}
    for paftolGene in analysisDatabase.paftolGeneDict.values():
        paftolGeneDict[paftolGene.geneName] = paftolGene
    for geneName in result.contigDict:
        if geneName not in paftolGeneDict:
            raise StandardError, 'found gene %s in result but it is not in the analysis database' % geneName
    ### Paul B - changed use auto_increment and to add in the CDS fasta filename rather than all the contigs (required a new Paftol.HybpiperResult object variable)
    #contigFastaFile = None
    reconstructedCdsFastaFname = None
    #if result.contigFastaFname is not None:
    #if result.reconstructedCdsFastaFname is not None:
        #contigFastaFile = paftol.database.analysis.FastaFile(None, result.contigFastaFname, paftol.tools.md5HexdigestFromFile(result.contigFastaFname), None, len(paftol.tools.fastaSeqRecordList(result.contigFastaFname)))
        # Paul B. - removed FastaFile, fasta contig file now goes into ContigRecovery table:
        #contigFastaFile = paftol.database.analysis.FastaFile(result.reconstructedCdsFastaFname, result.reconstructedCdsFastaFnamePath, paftol.tools.md5HexdigestFromFile(result.reconstructedCdsFastaFname), None, len(paftol.tools.fastaSeqRecordList(result.reconstructedCdsFastaFname)))
    ### Paul B - removed id=None first parameter to fit with the auto_increment change:
    #print "testing targetsFastaFile: ", result.paftolTargetSet.fastaHandleStr
    #contigRecovery = paftol.database.analysis.ContigRecovery(fwdFastq=fwdFastqFile, revFastq=revFastqFile, fwdTrimmedFastqStats=trimmedForwardFastqStats, revTrimmedFastqStats=trimmedReverseFastqStats, contigFastaFile=contigFastaFile, targetsFastaFile=targetsFastaFile, numMappedReads=numMappedReads, numUnmappedReads=numUnmappedReads, softwareVersion=paftol.__version__, cmdLine=result.cmdLine)
    # Paul B. - contig file info now goes into ContigRecovery table:
    contigRecovery = paftol.database.analysis.ContigRecovery(fwdFastq=fwdFastqFile, revFastq=revFastqFile, \
    fwdTrimmedFastqStats=trimmedForwardFastqStats, revTrimmedFastqStats=trimmedReverseFastqStats, \
    contigFastaFileName=result.reconstructedCdsFastaFname, contigFastaFilePathName=result.reconstructedCdsFastaFnamePath, contigFastaFileMd5sum=paftol.tools.md5HexdigestFromFile(result.reconstructedCdsFastaFname), \
    referenceTarget=targetsFastaFile, \
    numMappedReads=numMappedReads, numUnmappedReads=numUnmappedReads, softwareVersion=paftol.__version__, cmdLine=result.cmdLine)
    recoveredContigList = []
    ### Paul B. - Added info from result.reconstructedCdsDict to RecoveredContig table instead.
    ### NB - result.contigDict[geneName] is a list of contig BioSeqRecord objects but
    ### but result.reconstructedCdsDict[geneName] just contains a single supercontig BioSeqRecord object NOT in a list
    ### so don't need the contig for loop.
    #for geneName in result.contigDict:
    #   if result.contigDict[geneName] is not None and len(result.contigDict[geneName]) > 0:  # I think this is the numbr  of Seq records!!!
    #        for contig in result.contigDict[geneName]:
                # I think this accesses the id and seq values (not tested)
                #print "Contig.id ", contig.id   
                #print "contig.seq", contig.seq

    RC_Countr = 0   # Counting the number of recovered contigs so I can compare and check it with the value calculated by the db (to check that auto_increment is working)
    for geneName in result.reconstructedCdsDict:
        if result.reconstructedCdsDict[geneName] is not None and len(result.reconstructedCdsDict[geneName]) > 0:
            #print "result.reconstructedCdsDict[geneName].id: ", result.reconstructedCdsDict[geneName].id
            #print "Length of seq:", len(result.reconstructedCdsDict[geneName])
            #print "Seq:", result.reconstructedCdsDict[geneName].seq
            representativeReferenceTarget = findReferenceTarget(analysisDatabase, geneName, result.representativePaftolTargetDict[geneName].organism.name)
            if representativeReferenceTarget is None:
                raise StandardError, 'unknown reference target for geneName = %s, organismName = %s' % (geneName, result.representativePaftolTargetDict[geneName].organism.name)
            ### Paul B - removed 'None' first parameter to fit with the auto_increment change; changed from len(contig) to len(result.reconstructedCdsDict[geneName])
            recoveredContig = paftol.database.analysis.RecoveredContig(contigRecovery, paftolGeneDict[geneName], len(result.reconstructedCdsDict[geneName]), representativeReferenceTarget)
            recoveredContigList.append(recoveredContig)
            RC_Countr += 1
    contigRecovery.numRecoveredContigsCheck = RC_Countr
    transactionSuccessful = False
    ### Paul B. - can now remove table locking because now using auto_increment
    #lockCursor = connection.cursor(prepared=False)
    #lockCursor.execute('LOCK TABLE FastaFile WRITE, FastqFile WRITE, FastqStats WRITE, ContigRecovery WRITE, RecoveredContig WRITE')
    try:
        cursor = connection.cursor(prepared=True)
        try:
            ###  Paul B. - making changes to use auto_increment:
            if trimmedForwardFastqStats is not None:
                #trimmedForwardFastqStats.id = generateUnusedPrimaryKey(cursor, 'FastqStats')
                trimmedForwardFastqStats.insertIntoDatabase(cursor)
                trimmedForwardFastqStats.id = cursor.lastrowid
                if trimmedForwardFastqStats.id is not None:
                    print "trimmedForwardFastqStats.id: ", trimmedForwardFastqStats.id
            if trimmedReverseFastqStats is not None:
                #trimmedReverseFastqStats.id = generateUnusedPrimaryKey(cursor, 'FastqStats')
                trimmedReverseFastqStats.insertIntoDatabase(cursor)
                trimmedReverseFastqStats.id = cursor.lastrowid
                if trimmedReverseFastqStats.id is not None:
                    print "trimmedReverseFastqStats.id: ", trimmedReverseFastqStats.id
            # Paul B. - contigFastaFile now goes into ContigRecovery table (no need for conditional either? contigFastaFile value should just remain NULL
            #if contigFastaFile is not None:
                #contigFastaFile.id = generateUnusedPrimaryKey(cursor, 'FastaFile')
                #contigFastaFile.insertIntoDatabase(cursor)
                #contigFastaFile.id = cursor.lastrowid
                #print "contigFastaFile.id: ", contigFastaFile.id
            #contigRecovery.id = generateUnusedPrimaryKey(cursor, 'ContigRecovery')
            contigRecovery.insertIntoDatabase(cursor)
            contigRecovery.id = cursor.lastrowid
            if contigRecovery.id is not None:
                print "contigRecovery.id: ", contigRecovery.id
            for recoveredContig in recoveredContigList:
                #recoveredContig.id = generateUnusedPrimaryKey(cursor, 'RecoveredContig')
                recoveredContig.insertIntoDatabase(cursor)
                recoveredContig.id = cursor.lastrowid
                if recoveredContig.id is not None:
                    print "recoveredContig.id: ", recoveredContig.id
                #time.sleep(0.06)
            #### time delay - 1 second doen to 60milsec
            connection.commit()
            transactionSuccessful = True
        finally:
            if not transactionSuccessful:
                connection.rollback()
                # Paul B added:                                                                         # NB - these variables may not exist if commit fails
                print "ERROR: commit unsucessful for contigRecovery.id: "                               #, contigRecovery.id
                print "ERROR: commit unsucessful for trimmedForwardFastqStats.id: "                     #, trimmedForwardFastqStats.id
                print "ERROR: commit unsucessful for trimmedReverseFastqStats.id: "                     #, trimmedReverseFastqStats.id
                #print "ERROR: commit unsucessful for contigFastaFile.id: ", contigFastaFile.id
                print "ERROR: commit unsucessful for recoveredContig.id (for last row created): "       #, recoveredContig.id
            cursor.close()
    finally:
        if not transactionSuccessful:
            connection.rollback()
            # Paul B. removed this again- it should appear above:
            #print "ERROR: commit unsucessful for contigRecovery.id: ", contigRecovery.id
        #lockCursor.execute('UNLOCK TABLES')
        #lockCursor.close()
    connection.close()
    return transactionSuccessful


# def addExternalGenes(connection, analysisDatabase, fwdSeqFile=inputSequenceList[0]), fwdSeqFilePath:
#     ''' Adds required info for the paftol_da.ContigRecovery and paftol_da.RecoveredContig tables

#         4.8.2020 - Paul B. under development
#     ''' 
 
#     paftolGeneDict = {}
#     for paftolGene in analysisDatabase.paftolGeneDict.values():
#         paftolGeneDict[paftolGene.geneName] = paftolGene
#     for geneName in result.contigDict:
#         if geneName not in paftolGeneDict:
#             raise StandardError, 'found gene %s in result but it is not in the analysis database' % geneName
    


#     reconstructedCdsFastaFname = None
#     #if result.contigFastaFname is not None:
#     #if result.reconstructedCdsFastaFname is not None:
#         #contigFastaFile = paftol.database.analysis.FastaFile(None, result.contigFastaFname, paftol.tools.md5HexdigestFromFile(result.contigFastaFname), None, len(paftol.tools.fastaSeqRecordList(result.contigFastaFname)))
#         # Paul B. - removed FastaFile, fasta contig file now goes into ContigRecovery table:
#         #contigFastaFile = paftol.database.analysis.FastaFile(result.reconstructedCdsFastaFname, result.reconstructedCdsFastaFnamePath, paftol.tools.md5HexdigestFromFile(result.reconstructedCdsFastaFname), None, len(paftol.tools.fastaSeqRecordList(result.reconstructedCdsFastaFname)))
#     ### Paul B - removed id=None first parameter to fit with the auto_increment change:
#     #print "testing targetsFastaFile: ", result.paftolTargetSet.fastaHandleStr
#     #contigRecovery = paftol.database.analysis.ContigRecovery(fwdFastq=fwdFastqFile, revFastq=revFastqFile, fwdTrimmedFastqStats=trimmedForwardFastqStats, revTrimmedFastqStats=trimmedReverseFastqStats, contigFastaFile=contigFastaFile, targetsFastaFile=targetsFastaFile, numMappedReads=numMappedReads, numUnmappedReads=numUnmappedReads, softwareVersion=paftol.__version__, cmdLine=result.cmdLine)
#     # Paul B. - contig file info now goes into ContigRecovery table:
#     contigRecovery = paftol.database.analysis.ContigRecovery(fwdFastq=fwdSeqFile, revFastq=None, \
#     fwdTrimmedFastqStats=None, revTrimmedFastqStats=None, \
#     contigFastaFileName=fwdSeqFile, contigFastaFilePathName=fwdSeqFilePath, contigFastaFileMd5sum=paftol.tools.md5HexdigestFromFile(fwdSeqFile), \
#     referenceTarget=None, \
#     numMappedReads=None, numUnmappedReads=None, softwareVersion=None, cmdLine=None)
#     recoveredContigList = []
#     ### Paul B. - Added info from result.reconstructedCdsDict to RecoveredContig table instead.
#     ### NB - result.contigDict[geneName] is a list of contig BioSeqRecord objects but
#     ### but result.reconstructedCdsDict[geneName] just contains a single supercontig BioSeqRecord object NOT in a list
#     ### so don't need the contig for loop.
#     #for geneName in result.contigDict:
#     #   if result.contigDict[geneName] is not None and len(result.contigDict[geneName]) > 0:  # I think this is the numbr  of Seq records!!!
#     #        for contig in result.contigDict[geneName]:
#                 # I think this accesses the id and seq values (not tested)
#                 #print "Contig.id ", contig.id   
#                 #print "contig.seq", contig.seq


# ### Need to access equivalent of result.reconstructedCdsDict:
# ### Need to create a BioSeqRecord from the fasta filename and a dictionary with the geneName as key



#     RC_Countr = 0   # Counting the number of recovered contigs so I can compare and check it with the value calculated by the db (to check that auto_increment is working)
#     for geneName in result.reconstructedCdsDict:
#         if result.reconstructedCdsDict[geneName] is not None and len(result.reconstructedCdsDict[geneName]) > 0:
#             #print "result.reconstructedCdsDict[geneName].id: ", result.reconstructedCdsDict[geneName].id
#             #print "Length of seq:", len(result.reconstructedCdsDict[geneName])
#             #print "Seq:", result.reconstructedCdsDict[geneName].seq
#             #####DELETErepresentativeReferenceTarget = findReferenceTarget(analysisDatabase, geneName, result.representativePaftolTargetDict[geneName].organism.name)
#             #####DELETEif representativeReferenceTarget is None:
#              #####DELETE   raise StandardError, 'unknown reference target for geneName = %s, organismName = %s' % (geneName, result.representativePaftolTargetDict[geneName].organism.name)
#             ### Paul B - removed 'None' first parameter to fit with the auto_increment change; changed from len(contig) to len(result.reconstructedCdsDict[geneName])
#             recoveredContig = paftol.database.analysis.RecoveredContig(XXXX=contigRecovery, XXXX=paftolGeneDict[geneName], XXXX=len(result.reconstructedCdsDict[geneName]), XXXX=representativeReferenceTarget=None)
#             recoveredContigList.append(recoveredContig)
#             RC_Countr += 1
#     contigRecovery.numRecoveredContigsCheck = RC_Countr
# ##### UPTOHERE REMOVING CODE
#     transactionSuccessful = False
#     ### Paul B. - can now remove table locking because now using auto_increment
#     #lockCursor = connection.cursor(prepared=False)
#     #lockCursor.execute('LOCK TABLE FastaFile WRITE, FastqFile WRITE, FastqStats WRITE, ContigRecovery WRITE, RecoveredContig WRITE')
#     try:
#         cursor = connection.cursor(prepared=True)
#         try:
#             ###  Paul B. - making changes to use auto_increment:
#             if trimmedForwardFastqStats is not None:
#                 #trimmedForwardFastqStats.id = generateUnusedPrimaryKey(cursor, 'FastqStats')
#                 trimmedForwardFastqStats.insertIntoDatabase(cursor)
#                 trimmedForwardFastqStats.id = cursor.lastrowid
#                 if trimmedForwardFastqStats.id is not None:
#                     print "trimmedForwardFastqStats.id: ", trimmedForwardFastqStats.id
#             if trimmedReverseFastqStats is not None:
#                 #trimmedReverseFastqStats.id = generateUnusedPrimaryKey(cursor, 'FastqStats')
#                 trimmedReverseFastqStats.insertIntoDatabase(cursor)
#                 trimmedReverseFastqStats.id = cursor.lastrowid
#                 if trimmedReverseFastqStats.id is not None:
#                     print "trimmedReverseFastqStats.id: ", trimmedReverseFastqStats.id
#             # Paul B. - contigFastaFile now goes into ContigRecovery table (no need for conditional either? contigFastaFile value should just remain NULL
#             #if contigFastaFile is not None:
#                 #contigFastaFile.id = generateUnusedPrimaryKey(cursor, 'FastaFile')
#                 #contigFastaFile.insertIntoDatabase(cursor)
#                 #contigFastaFile.id = cursor.lastrowid
#                 #print "contigFastaFile.id: ", contigFastaFile.id
#             #contigRecovery.id = generateUnusedPrimaryKey(cursor, 'ContigRecovery')
#             contigRecovery.insertIntoDatabase(cursor)
#             contigRecovery.id = cursor.lastrowid
#             if contigRecovery.id is not None:
#                 print "contigRecovery.id: ", contigRecovery.id
#             for recoveredContig in recoveredContigList:
#                 #recoveredContig.id = generateUnusedPrimaryKey(cursor, 'RecoveredContig')
#                 recoveredContig.insertIntoDatabase(cursor)
#                 recoveredContig.id = cursor.lastrowid
#                 if recoveredContig.id is not None:
#                     print "recoveredContig.id: ", recoveredContig.id
#                 #time.sleep(0.06)
#             #### time delay - 1 second doen to 60milsec
#             connection.commit()
#             transactionSuccessful = True
#         finally:
#             if not transactionSuccessful:
#                 connection.rollback()
#                 # Paul B added:                                                                         # NB - these variables may not exist if commit fails
#                 print "ERROR: commit unsucessful for contigRecovery.id: "                               #, contigRecovery.id
#                 print "ERROR: commit unsucessful for trimmedForwardFastqStats.id: "                     #, trimmedForwardFastqStats.id
#                 print "ERROR: commit unsucessful for trimmedReverseFastqStats.id: "                     #, trimmedReverseFastqStats.id
#                 #print "ERROR: commit unsucessful for contigFastaFile.id: ", contigFastaFile.id
#                 print "ERROR: commit unsucessful for recoveredContig.id (for last row created): "       #, recoveredContig.id
#             cursor.close()
#     finally:
#         if not transactionSuccessful:
#             connection.rollback()
#             # Paul B. removed this again- it should appear above:
#             #print "ERROR: commit unsucessful for contigRecovery.id: ", contigRecovery.id
#         #lockCursor.execute('UNLOCK TABLES')
#         #lockCursor.close()
#     connection.close()
#     return transactionSuccessful